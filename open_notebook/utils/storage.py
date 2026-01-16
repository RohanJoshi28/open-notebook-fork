import os
from pathlib import Path
from typing import Optional
from uuid import uuid4

from loguru import logger

from open_notebook.config import GCS_BUCKET, STORAGE_BACKEND, UPLOADS_FOLDER


def generate_unique_filename(original_filename: str, upload_folder: str) -> str:
    """
    Generate a unique filename within upload_folder, appending a counter if needed.
    Mirrors the logic used by the upload API so Drive imports behave consistently.
    """
    file_path = Path(upload_folder)
    file_path.mkdir(parents=True, exist_ok=True)

    stem = Path(original_filename).stem
    suffix = Path(original_filename).suffix

    counter = 0
    while True:
        candidate = f"{stem} ({counter}){suffix}" if counter else original_filename
        full_path = file_path / candidate
        if not full_path.exists():
            return str(full_path)
        counter += 1


def save_bytes_to_storage(data: bytes, filename: str, prefix: Optional[str] = None) -> str:
    """
    Save raw bytes to the configured storage backend.

    Returns:
        - local: absolute path under UPLOADS_FOLDER
        - gcs: gs://bucket/key
    """
    target_prefix = prefix or "uploads"

    if STORAGE_BACKEND == "gcs":
        if not GCS_BUCKET:
            raise RuntimeError("GCS_BUCKET_NAME is not configured")
        try:
            from google.cloud import storage  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("google-cloud-storage is required for GCS uploads") from exc

        client = storage.Client()
        blob_path = f"{target_prefix}/{uuid4()}_{filename}"
        blob = client.bucket(GCS_BUCKET).blob(blob_path)
        blob.upload_from_string(data)
        gcs_uri = f"gs://{GCS_BUCKET}/{blob_path}"
        logger.info(f"Saved bytes to GCS: {gcs_uri}")
        return gcs_uri

    # Local filesystem
    unique_path = generate_unique_filename(filename, UPLOADS_FOLDER)
    with open(unique_path, "wb") as f:
        f.write(data)
    logger.info(f"Saved bytes locally: {unique_path}")
    return unique_path
