from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import get_current_user_id
from open_notebook.utils.google_drive import (
    GoogleDriveClient,
    export_mime_for,
    parse_drive_url,
)

router = APIRouter(prefix="/drive", tags=["drive"])


class DriveResolveRequest(BaseModel):
    url: str = Field(..., description="Google Drive file or folder URL")
    recursive: bool = Field(True, description="Recursively include subfolders")
    max_items: int = Field(500, description="Maximum files to return")


class DriveResolvedItem(BaseModel):
    id: str
    name: str
    mime_type: str
    resource_key: Optional[str] = None
    is_google_doc: bool = False
    export_mime_type: Optional[str] = None
    web_view_url: Optional[str] = None


class DriveResolveResponse(BaseModel):
    kind: str  # "file" or "folder"
    items: List[DriveResolvedItem]


@router.post("/resolve", response_model=DriveResolveResponse)
async def resolve_drive_link(
    payload: DriveResolveRequest = Body(...),
    user_id: str = Depends(get_current_user_id),
):
    """
    Resolve a Google Drive link into concrete file entries.
    - File link -> returns single item.
    - Folder link -> returns flattened list of files (recursively by default).
    Requires the user to have granted Drive read scopes.
    """
    link = parse_drive_url(payload.url)
    if not link:
        raise HTTPException(status_code=400, detail="Not a valid Google Drive link")

    logger.info(
        "drive.resolve start user=%s url=%s kind=%s id=%s resource_key=%s recursive=%s max_items=%s",
        user_id,
        payload.url,
        getattr(link, "kind", None),
        getattr(link, "id", None),
        getattr(link, "resource_key", None),
        payload.recursive,
        payload.max_items,
    )

    try:
        client = await GoogleDriveClient.from_user(user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        logger.error(f"Failed to init Drive client: {exc}")
        raise HTTPException(status_code=500, detail="Failed to initialize Drive access")

    try:
        if link.kind == "file":
            meta = await client.get_file_metadata(link.id, link.resource_key)
            meta = await client.resolve_drive_file(meta)
            item = DriveResolvedItem(
                id=meta.id,
                name=meta.name,
                mime_type=meta.mime_type,
                resource_key=meta.resource_key,
                is_google_doc=meta.mime_type.startswith("application/vnd.google-apps."),
                export_mime_type=export_mime_for(meta.mime_type),
                web_view_url=payload.url,
            )
            return DriveResolveResponse(kind="file", items=[item])

        # Folder path
        files = await client.list_children(
            folder_id=link.id,
            resource_key=link.resource_key,
            recursive=payload.recursive,
            max_items=payload.max_items,
        )
        resolved_items = [
            DriveResolvedItem(
                id=f.id,
                name=f.name,
                mime_type=f.mime_type,
                resource_key=f.resource_key,
                is_google_doc=f.mime_type.startswith("application/vnd.google-apps."),
                export_mime_type=export_mime_for(f.mime_type),
                web_view_url=payload.url,
            )
            for f in files
        ]
        return DriveResolveResponse(kind="folder", items=resolved_items)
    except HTTPException:
        raise
    except PermissionError as exc:
        logger.error(
            "Drive resolve failed permission user=%s link=%s id=%s resource_key=%s err=%s",
            user_id,
            payload.url,
            getattr(link, "id", None),
            getattr(link, "resource_key", None),
            exc,
        )
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:
        logger.exception(f"Drive resolve failed: {exc}")
        raise HTTPException(status_code=500, detail="Failed to resolve Drive link")
