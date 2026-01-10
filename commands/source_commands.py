import os
import tempfile
import time
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel
from surreal_commands import CommandInput, CommandOutput, command

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import Source
from open_notebook.domain.transformation import Transformation
from open_notebook.config import GCS_BUCKET, STORAGE_BACKEND

try:
    from open_notebook.graphs.source import source_graph
except ImportError as e:
    logger.error(f"Failed to import source_graph: {e}")
    source_graph = None


def full_model_dump(model):
    if isinstance(model, BaseModel):
        return model.model_dump()
    elif isinstance(model, dict):
        return {k: full_model_dump(v) for k, v in model.items()}
    elif isinstance(model, list):
        return [full_model_dump(item) for item in model]
    else:
        return model


class SourceProcessingInput(CommandInput):
    source_id: str
    content_state: Dict[str, Any]
    notebook_ids: List[str]
    transformations: List[str]
    embed: bool
    owner: Optional[str] = None


class SourceProcessingOutput(CommandOutput):
    success: bool
    source_id: str
    embedded_chunks: int = 0
    insights_created: int = 0
    processing_time: float
    error_message: Optional[str] = None


def _materialize_content_file(content_state: Dict[str, Any]) -> None:
    """
    Ensure content_state.file_path points to a local file.
    If using GCS storage, download to a temp file for processing.
    """
    file_path = content_state.get("file_path")
    if not file_path or not isinstance(file_path, str):
        return

    # Preserve original path for later download links / deletion
    content_state.setdefault("_orig_file_path", file_path)

    if file_path.startswith("gs://"):
        try:
            from google.cloud import storage  # type: ignore
        except Exception as exc:
            raise RuntimeError("google-cloud-storage is required to process GCS files") from exc

        client = storage.Client()
        bucket_name, blob_path = file_path[5:].split("/", 1)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        if not blob.exists():
            raise FileNotFoundError(f"GCS file not found: {file_path}")

        tmp = tempfile.NamedTemporaryFile(delete=False)
        blob.download_to_filename(tmp.name)
        content_state["file_path"] = tmp.name
        content_state["_temp_download_path"] = tmp.name
        logger.debug(f"Downloaded GCS file {file_path} to temp {tmp.name}")
    else:
        # Local path: verify existence for clearer errors
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")


@command(
    "process_source",
    app="open_notebook",
    retry={
        "max_attempts": 5,
        "wait_strategy": "exponential_jitter",
        "wait_min": 1,
        "wait_max": 30,
        "retry_on": [RuntimeError],
    },
)
async def process_source_command(
    input_data: SourceProcessingInput,
) -> SourceProcessingOutput:
    """
    Process source content using the source_graph workflow
    """
    start_time = time.time()

    try:
        logger.info(
            "process_source_command: source=%s notebooks=%s embed=%s transformations=%s content_keys=%s",
            input_data.source_id,
            input_data.notebook_ids,
            input_data.embed,
            input_data.transformations,
            list(input_data.content_state.keys() if input_data.content_state else []),
        )
        logger.info(f"Starting source processing for source: {input_data.source_id}")
        logger.info(f"Notebook IDs: {input_data.notebook_ids}")
        logger.info(f"Transformations: {input_data.transformations}")
        logger.info(f"Embed: {input_data.embed}")

        # Ensure file is available locally (handles GCS-backed uploads)
        if input_data.content_state:
            _materialize_content_file(input_data.content_state)
        original_file_path = input_data.content_state.get("_orig_file_path") if input_data.content_state else None

        # 1. Load transformation objects from IDs (shared or same owner)
        transformations = []
        for trans_id in input_data.transformations:
            logger.info(f"Loading transformation: {trans_id}")
            transformation = await Transformation.get(trans_id)
            if not transformation:
                raise ValueError(f"Transformation '{trans_id}' not found")
            if transformation.owner is not None and input_data.owner is not None and str(transformation.owner) != str(input_data.owner):
                raise ValueError(f"Transformation '{trans_id}' not accessible for this user")
            transformations.append(transformation)

        logger.info(f"Loaded {len(transformations)} transformations")

        # 2. Get existing source record to update its command field
        source = await Source.get(input_data.source_id)
        if not source:
            raise ValueError(f"Source '{input_data.source_id}' not found")

        # Update source with command reference
        source.command = (
            ensure_record_id(input_data.execution_context.command_id)
            if input_data.execution_context
            else None
        )
        await source.save()

        logger.info(f"Updated source {source.id} with command reference")

        # Fallback path when source_graph is unavailable (e.g., optional deps missing)
        if source_graph is None:
            logger.warning("source_graph unavailable; using fallback inline processing.")

            # Write full_text directly from provided content_state
            content_state = input_data.content_state or {}
            text_content = content_state.get("content")
            if not text_content and content_state.get("file_path"):
                try:
                    with open(content_state["file_path"], "r", encoding="utf-8", errors="ignore") as f:
                        text_content = f.read()
                except Exception as exc:
                    logger.error(f"Failed to read file for fallback processing: {exc}")
                    raise

            source.full_text = text_content or ""
            await source.save()

            # Link to notebooks
            for nb in input_data.notebook_ids or []:
                try:
                    await source.add_to_notebook(nb)
                except Exception as exc:
                    logger.warning(f"Failed to add source to notebook {nb}: {exc}")

            embedded_chunks = 0
            # Minimal embedding pipeline so RAG still works without optional deps
            if input_data.embed and source.full_text:
                try:
                    from open_notebook.domain.models import model_manager
                    from open_notebook.utils.text_utils import split_text

                    EMBEDDING_MODEL = await model_manager.get_embedding_model()
                    if EMBEDDING_MODEL:
                        chunks = split_text(source.full_text)
                        owner_id = ensure_record_id(source.owner) if source.owner else None
                        for idx, chunk in enumerate(chunks):
                            embedding = (await EMBEDDING_MODEL.aembed([chunk]))[0]
                            await repo_query(
                                """
                                CREATE source_embedding CONTENT {
                                    "source": $source_id,
                                    "order": $order,
                                    "content": $content,
                                    "embedding": $embedding,
                                    "owner": $owner,
                                };
                                """,
                                {
                                    "source_id": ensure_record_id(source.id),
                                    "order": idx,
                                    "content": chunk,
                                    "embedding": embedding,
                                    "owner": owner_id,
                                },
                            )
                        embedded_chunks = len(chunks)
                    else:
                        logger.warning("Embedding model not configured; skipping embeddings in fallback path.")
                except Exception as exc:
                    logger.error(f"Fallback embedding failed: {exc}")

            processing_time = time.time() - start_time
            return SourceProcessingOutput(
                success=True,
                source_id=source.id or "",
                embedded_chunks=embedded_chunks,
                insights_created=0,
                processing_time=processing_time,
                error_message=None,
            )

        # 3. Process source with all notebooks (normal path)
        logger.info(f"Processing source with {len(input_data.notebook_ids)} notebooks")

        # Execute source_graph with all notebooks (guarded by timeout to avoid stuck commands)
        from asyncio import wait_for, TimeoutError as AsyncTimeoutError
        processing_timeout = int(os.getenv("SOURCE_PROCESS_TIMEOUT", "300"))
        try:
            result = await wait_for(
                source_graph.ainvoke(
                    {  # type: ignore[arg-type]
                        "content_state": input_data.content_state,
                        "notebook_ids": input_data.notebook_ids,  # Use notebook_ids (plural) as expected by SourceState
                        "apply_transformations": transformations,
                        "embed": input_data.embed,
                        "source_id": input_data.source_id,  # Add the source_id to the state
                    }
                ),
                timeout=processing_timeout,
            )
        except AsyncTimeoutError:
            raise TimeoutError(
                f"Source processing exceeded {processing_timeout} seconds"
            )

        processed_source = result["source"]

        # 4. Gather processing results (notebook associations handled by source_graph)
        embedded_chunks = (
            await processed_source.get_embedded_chunks() if input_data.embed else 0
        )
        insights_list = await processed_source.get_insights()
        insights_created = len(insights_list)
        logger.info(
            "process_source_command completed: source=%s embedded_chunks=%s insights=%s",
            processed_source.id,
            embedded_chunks,
            insights_created,
        )

        processing_time = time.time() - start_time
        logger.info(
            f"Successfully processed source: {processed_source.id} in {processing_time:.2f}s"
        )
        logger.info(
            f"Created {insights_created} insights and {embedded_chunks} embedded chunks"
        )

        return SourceProcessingOutput(
            success=True,
            source_id=str(processed_source.id),
            embedded_chunks=embedded_chunks,
            insights_created=insights_created,
            processing_time=processing_time,
        )

    except RuntimeError as e:
        # Transaction conflicts should be retried by surreal-commands
        logger.warning(f"Transaction conflict, will retry: {e}")
        raise

    except Exception as e:
        # Other errors are permanent failures
        processing_time = time.time() - start_time
        logger.error(f"Source processing failed: {e}")

        return SourceProcessingOutput(
            success=False,
            source_id=input_data.source_id,
            processing_time=processing_time,
            error_message=str(e),
        )
    finally:
        temp_path = None
        try:
            temp_path = input_data.content_state.get("_temp_download_path") if input_data.content_state else None
        except Exception:
            temp_path = None
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                logger.warning(f"Failed to delete temp file {temp_path}")
