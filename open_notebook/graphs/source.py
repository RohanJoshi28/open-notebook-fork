import operator
import os
from typing import Any, Dict, List, Optional

from content_core import extract_content
from content_core.common import ProcessSourceState
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from loguru import logger
from typing_extensions import Annotated, TypedDict

from open_notebook.domain.content_settings import ContentSettings
from open_notebook.domain.models import Model, ModelManager
from open_notebook.domain.notebook import Asset, Source
from open_notebook.domain.transformation import Transformation
from open_notebook.graphs.transformation import graph as transform_graph


class SourceState(TypedDict):
    content_state: ProcessSourceState
    apply_transformations: List[Transformation]
    source_id: str
    notebook_ids: List[str]
    source: Source
    transformation: Annotated[list, operator.add]
    embed: bool


class TransformationState(TypedDict):
    source: Source
    transformation: Transformation


async def content_process(state: SourceState) -> dict:
    content_settings = ContentSettings(
        default_content_processing_engine_doc="auto",
        default_content_processing_engine_url="auto",
        default_embedding_option="ask",
        auto_delete_files="yes",
        youtube_preferred_languages=["en", "pt", "es", "de", "nl", "en-GB", "fr", "hi", "ja"]
    )
    content_state: Dict[str, Any] = state["content_state"]  # type: ignore[assignment]

    # Preserve meta flags (_orig_file_path, delete_source, etc.) to re-attach after extraction
    meta_keys = {k: v for k, v in content_state.items() if k.startswith("_") or k == "delete_source"}

    content_state["url_engine"] = (
        content_settings.default_content_processing_engine_url or "auto"
    )
    content_state["document_engine"] = (
        content_settings.default_content_processing_engine_doc or "auto"
    )
    content_state["output_format"] = "markdown"

    # Add speech-to-text model configuration from Default Models
    try:
        model_manager = ModelManager()
        defaults = await model_manager.get_defaults()
        if defaults.default_speech_to_text_model:
            stt_model = await Model.get(defaults.default_speech_to_text_model)
            if stt_model:
                content_state["audio_provider"] = stt_model.provider
                content_state["audio_model"] = stt_model.name
                logger.debug(f"Using speech-to-text model: {stt_model.provider}/{stt_model.name}")
    except Exception as e:
        logger.warning(f"Failed to retrieve speech-to-text model configuration: {e}")
        # Continue without custom audio model (content-core will use its default)

    processed_state = await extract_content(content_state)

    # Convert to plain dict if extract_content returns a Pydantic/BaseModel-like object
    if hasattr(processed_state, "model_dump"):
        processed_state = processed_state.model_dump()  # type: ignore[assignment]

    # Re-attach meta flags so downstream nodes can access them
    if isinstance(processed_state, dict):
        processed_state.update(meta_keys)

    return {"content_state": processed_state}


async def save_source(state: SourceState) -> dict:
    content_state = state["content_state"]

    # Get existing source using the provided source_id
    source = await Source.get(state["source_id"])
    if not source:
        raise ValueError(f"Source with ID {state['source_id']} not found")

    # Update the source with processed content
    original_path = content_state.get("_orig_file_path") if isinstance(content_state, dict) else getattr(content_state, "_orig_file_path", None)

    # Extract fields in a dict-friendly way so we don't blow up when extract_content returns a plain dict
    def _cs_val(key: str):
        if isinstance(content_state, dict):
            return content_state.get(key)
        return getattr(content_state, key, None)

    source.asset = Asset(
        url=original_path or _cs_val("url"),
        file_path=original_path or _cs_val("file_path"),
    )
    source.full_text = _cs_val("content")
    
    # Preserve user-provided title; only override if extracted title exists AND current title is missing or placeholder
    title_val = _cs_val("title")
    if title_val:
        if not source.title or source.title.strip().lower() in {"processing...", ""}:
            source.title = title_val
    
    await source.save()

    # NOTE: Notebook associations are created by the API immediately for UI responsiveness
    # No need to create them here to avoid duplicate edges

    if state["embed"]:
        logger.debug("Embedding content for vector search")
        await source.vectorize()

    # Optionally delete original source file after processing
    if isinstance(content_state, dict):
        delete_source_flag = content_state.get("delete_source")
    else:
        delete_source_flag = getattr(content_state, "delete_source", None)

    if delete_source_flag and original_path:
        try:
            if original_path.startswith("gs://"):
                from google.cloud import storage  # type: ignore

                bucket_name, blob_path = original_path[5:].split("/", 1)
                storage.Client().bucket(bucket_name).blob(blob_path).delete(if_exists=True)
            else:
                if os.path.exists(original_path):
                    os.unlink(original_path)
            # Clear stored path since file is gone
            source.asset = Asset(url=None, file_path=None)
            await source.save()
        except Exception as e:
            logger.warning(f"Failed to delete source file {original_path}: {e}")

    return {"source": source}


def trigger_transformations(state: SourceState, config: RunnableConfig) -> List[Send]:
    if len(state["apply_transformations"]) == 0:
        return []

    to_apply = state["apply_transformations"]
    logger.debug(f"Applying transformations {to_apply}")

    return [
        Send(
            "transform_content",
            {
                "source": state["source"],
                "transformation": t,
            },
        )
        for t in to_apply
    ]


async def transform_content(state: TransformationState) -> Optional[dict]:
    source = state["source"]
    content = source.full_text
    if not content:
        return None
    transformation: Transformation = state["transformation"]

    logger.debug(f"Applying transformation {transformation.name}")
    result = await transform_graph.ainvoke(
        dict(input_text=content, transformation=transformation)  # type: ignore[arg-type]
    )
    await source.add_insight(transformation.title, result["output"], owner=source.owner)
    return {
        "transformation": [
            {
                "output": result["output"],
                "transformation_name": transformation.name,
            }
        ]
    }


# Create and compile the workflow
workflow = StateGraph(SourceState)

# Add nodes
workflow.add_node("content_process", content_process)
workflow.add_node("save_source", save_source)
workflow.add_node("transform_content", transform_content)
# Define the graph edges
workflow.add_edge(START, "content_process")
workflow.add_edge("content_process", "save_source")
workflow.add_conditional_edges(
    "save_source", trigger_transformations, ["transform_content"]
)
workflow.add_edge("transform_content", END)

# Compile the graph
source_graph = workflow.compile()
