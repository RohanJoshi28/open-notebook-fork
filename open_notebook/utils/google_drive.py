import asyncio
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger

from open_notebook.domain.google_credential import GoogleCredential

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]


@dataclass
class DriveLink:
    id: str
    kind: str  # "file" or "folder"
    resource_key: Optional[str]
    url: str


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str
    resource_key: Optional[str] = None
    drive_id: Optional[str] = None
    is_shortcut: bool = False
    shortcut_target_id: Optional[str] = None
    shortcut_resource_key: Optional[str] = None


_EXPORT_MIME_MAP: Dict[str, str] = {
    "application/vnd.google-apps.document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.google-apps.presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.google-apps.drawing": "image/png",
}


def parse_drive_url(url: str) -> Optional[DriveLink]:
    """
    Parse a Google Drive/Docs URL and return (id, kind, resource_key).
    Supports file links, folder links, and open?id= / uc?id= patterns.
    """
    logger.debug("drive.parse_drive_url start url=%s", url)
    try:
        resource_key = None
        if "resourcekey" in url.lower():
            match = re.search(r"[?&]resourcekey=([^&#]+)", url, flags=re.IGNORECASE)
            if match:
                resource_key = match.group(1)
                logger.debug("drive.parse_drive_url extracted resource_key len=%s", len(resource_key))

        # Folder pattern
        folder_match = re.search(r"/drive/folders/([a-zA-Z0-9_-]+)", url)
        if folder_match:
            return DriveLink(id=folder_match.group(1), kind="folder", resource_key=resource_key, url=url)

        # File pattern: drive file URL
        file_match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
        if file_match:
            return DriveLink(id=file_match.group(1), kind="file", resource_key=resource_key, url=url)

        # Docs/Sheets/Slides URLs
        doc_match = re.search(r"/document/d/([a-zA-Z0-9_-]+)", url)
        sheet_match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
        slide_match = re.search(r"/presentation/d/([a-zA-Z0-9_-]+)", url)
        for m in (doc_match, sheet_match, slide_match):
            if m:
                logger.debug("drive.parse_drive_url matched docs/sheets/slides kind=file id=%s", m.group(1))
                return DriveLink(id=m.group(1), kind="file", resource_key=resource_key, url=url)

        # open?id=
        q_match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url)
        if q_match:
            return DriveLink(id=q_match.group(1), kind="file", resource_key=resource_key, url=url)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"parse_drive_url failed for {url}: {exc}")
    return None


def build_resource_key_header(file_id: str, resource_key: Optional[str]) -> Dict[str, str]:
    if not resource_key:
        return {}
    return {"X-Goog-Drive-Resource-Keys": f"{file_id}/{resource_key}"}


class GoogleDriveClient:
    def __init__(self, user_id: str, credential: GoogleCredential):
        self.user_id = user_id
        self.credential = credential
        self.client_id = os.environ.get("GOOGLE_CLIENT_ID")
        self.client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
        if not self.client_id or not self.client_secret:
            raise RuntimeError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be configured for Drive access")
        self._lock = asyncio.Lock()

    @classmethod
    async def from_user(cls, user_id: str) -> "GoogleDriveClient":
        cred = await cls._get_credentials(user_id)
        if not cred:
            raise PermissionError("Drive access has not been granted. Please sign in again with Drive permissions.")
        return cls(user_id, cred)

    @staticmethod
    async def _get_credentials(user_id: str) -> Optional[GoogleCredential]:
        try:
            from open_notebook.database.repository import repo_query, ensure_record_id

            result = await repo_query(
                "SELECT * FROM google_credential WHERE user = $user LIMIT 1",
                {"user": ensure_record_id(user_id)},
            )
            if result:
                logger.debug(
                    "drive._get_credentials hit user=%s scope_len=%s has_refresh=%s has_access=%s",
                    user_id,
                    len(result[0].get("scope", "").split()) if result[0].get("scope") else 0,
                    bool(result[0].get("refresh_token")),
                    bool(result[0].get("access_token")),
                )
                return GoogleCredential(**result[0])
            logger.error("drive client init: no credential row found for user=%s", user_id)
        except Exception as exc:
            logger.error(f"Failed to load Drive credentials: {exc}")
        return None

    async def _refresh_access_token(self) -> str:
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.credential.refresh_token,
        }
        logger.debug(
            "drive: refreshing access token for user=%s refresh_len=%s scope_len_prev=%s",
            self.user_id,
            len(self.credential.refresh_token or ""),
            len(self.credential.scope.split()) if self.credential.scope else 0,
        )
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("https://oauth2.googleapis.com/token", data=payload)
        if resp.status_code != 200:
            logger.error(f"Token refresh failed: {resp.status_code} {resp.text}")
            raise PermissionError("Failed to refresh Google Drive token. Please re-authenticate.")

        data = resp.json()
        access_token = data.get("access_token")
        expires_in = data.get("expires_in", 3600)
        token_type = data.get("token_type", "Bearer")
        scope = data.get("scope", self.credential.scope)

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in) - 60)
        self.credential.access_token = access_token
        self.credential.expires_at = expires_at.isoformat()
        self.credential.scope = scope
        self.credential.token_type = token_type
        await self.credential.save()
        logger.debug("drive: refreshed token exp=%s scope_len=%s", expires_at, len(scope.split()) if scope else 0)
        return access_token

    async def get_access_token(self) -> str:
        async with self._lock:
            token = self.credential.access_token
            expires_at = self.credential.expires_at
            if isinstance(expires_at, str):
                try:
                    expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                except Exception:
                    expires_at = None
            if not token or (expires_at and expires_at <= datetime.now(timezone.utc)):
                logger.debug(
                    "drive.get_access_token expired_or_missing=%s expires_at=%s now=%s",
                    not bool(token) or bool(expires_at and expires_at <= datetime.now(timezone.utc)),
                    expires_at,
                    datetime.now(timezone.utc),
                )
                return await self._refresh_access_token()
            return token

    async def _headers(self, resource_key: Optional[str] = None, file_id: Optional[str] = None) -> Dict[str, str]:
        token = await self.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        if file_id:
            headers.update(build_resource_key_header(file_id, resource_key))
        return headers

    async def get_file_metadata(self, file_id: str, resource_key: Optional[str] = None) -> DriveFile:
        params = {
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "fields": "id,name,mimeType,resourceKey,shortcutDetails,driveId,md5Checksum,size",
        }
        headers = await self._headers(resource_key, file_id)
        logger.debug(
            "drive.get_file_metadata start id=%s resource_key_len=%s headers_have_key=%s",
            file_id,
            len(resource_key) if resource_key else 0,
            "X-Goog-Drive-Resource-Keys" in headers,
        )
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                params=params,
                headers=headers,
            )
        if resp.status_code in (403, 404):
            logger.error(
                "drive get_file_metadata status={} id={} url={} body={}",
                resp.status_code,
                file_id,
                resp.url,
                resp.text,
            )
            detail_msg = None
            try:
                detail_msg = resp.json().get("error", {}).get("message")
            except Exception:
                detail_msg = resp.text
            msg = (
                "Drive file not found or access denied"
                if resp.status_code == 404
                else f"Drive access forbidden; resource key or permissions may be missing. Google said: {detail_msg}"
            )
            raise PermissionError(msg)
        resp.raise_for_status()
        data = resp.json()

        mime_type = data.get("mimeType", "")
        is_shortcut = mime_type == "application/vnd.google-apps.shortcut"
        target = data.get("shortcutDetails", {}) if isinstance(data.get("shortcutDetails"), dict) else {}

        return DriveFile(
            id=data.get("id"),
            name=data.get("name"),
            mime_type=mime_type,
            resource_key=data.get("resourceKey") or resource_key,
            drive_id=data.get("driveId"),
            is_shortcut=is_shortcut,
            shortcut_target_id=target.get("targetId"),
            shortcut_resource_key=target.get("targetResourceKey"),
        )

    async def resolve_drive_file(self, file: DriveFile) -> DriveFile:
        """
        Follow shortcuts transparently.
        """
        if not file.is_shortcut or not file.shortcut_target_id:
            return file
        return await self.get_file_metadata(file.shortcut_target_id, file.shortcut_resource_key)

    async def list_children(
        self,
        folder_id: str,
        resource_key: Optional[str] = None,
        recursive: bool = True,
        max_items: int = 500,
    ) -> List[DriveFile]:
        # First fetch folder metadata to pick correct corpora/driveId (required for shared drives)
        folder_meta = await self.get_file_metadata(folder_id, resource_key)
        folder_meta = await self.resolve_drive_file(folder_meta)

        headers = await self._headers(folder_meta.resource_key, folder_id)
        logger.debug(
            "drive.list_children start folder=%s driveId=%s resource_key_len=%s recursive=%s max_items=%s",
            folder_id,
            folder_meta.drive_id,
            len(folder_meta.resource_key) if folder_meta.resource_key else 0,
            recursive,
            max_items,
        )
        query = f"'{folder_id}' in parents and trashed = false"
        params = {
            "q": query,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "pageSize": 1000,
            "fields": "nextPageToken, files(id,name,mimeType,resourceKey,driveId,shortcutDetails)",
        }
        # Use drive corpora when listing inside a shared drive
        if folder_meta.drive_id:
            params["corpora"] = "drive"
            params["driveId"] = folder_meta.drive_id
        else:
            params["corpora"] = "user"

        items: List[DriveFile] = []
        page_token: Optional[str] = None
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                if page_token:
                    params["pageToken"] = page_token
                logger.debug(
                    "drive.list_children page_request folder=%s token_present=%s",
                    folder_id,
                    bool(page_token),
                )
                resp = await client.get(
                    "https://www.googleapis.com/drive/v3/files",
                    params=params,
                    headers=headers,
                )
                if resp.status_code in (403, 404):
                    logger.error(
                        "drive list_children folder={} driveId={} status={} url={} body={}",
                        folder_id,
                        folder_meta.drive_id,
                        resp.status_code,
                        resp.url,
                        resp.text,
                    )
                    detail = None
                    try:
                        detail = resp.json().get("error", {}).get("message")
                    except Exception:
                        detail = resp.text
                    raise PermissionError(
                        f"Unable to list folder contents; access denied or link invalid. Google said: {detail}"
                    )
                resp.raise_for_status()
                data = resp.json()
                logger.debug(
                    "drive.list_children page received count=%s next=%s",
                    len(data.get("files", [])),
                    bool(data.get("nextPageToken")),
                )
                for f in data.get("files", []):
                    drive_file = DriveFile(
                        id=f.get("id"),
                        name=f.get("name"),
                        mime_type=f.get("mimeType"),
                        resource_key=f.get("resourceKey"),
                        drive_id=f.get("driveId"),
                        is_shortcut=f.get("mimeType") == "application/vnd.google-apps.shortcut",
                        shortcut_target_id=(f.get("shortcutDetails") or {}).get("targetId"),
                        shortcut_resource_key=(f.get("shortcutDetails") or {}).get("targetResourceKey"),
                    )
                    resolved = await self.resolve_drive_file(drive_file)
                    if resolved.mime_type == "application/vnd.google-apps.folder":
                        if recursive:
                            sub_items = await self.list_children(
                                resolved.id,
                                resolved.resource_key,
                                recursive=recursive,
                                max_items=max_items - len(items),
                            )
                            items.extend(sub_items)
                            if len(items) >= max_items:
                                return items
                        continue
                    items.append(resolved)
                    if len(items) >= max_items:
                        return items
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
        return items

    async def download_file(
        self,
        file: DriveFile,
        export_mime: Optional[str] = None,
    ) -> Tuple[bytes, str, str]:
        """
        Download or export a Drive file.

        Returns: (content_bytes, filename, content_type)
        """
        target = await self.resolve_drive_file(file)
        mime = target.mime_type
        headers = await self._headers(target.resource_key, target.id)
        logger.debug(
            "drive.download_file start id=%s mime=%s is_shortcut=%s resource_key_len=%s",
            target.id,
            mime,
            target.is_shortcut,
            len(target.resource_key) if target.resource_key else 0,
        )

        # Google Docs editors require export
        if mime.startswith("application/vnd.google-apps."):
            export_as = export_mime or _EXPORT_MIME_MAP.get(mime)
            if not export_as:
                raise ValueError(f"No export format configured for {mime}")
            extension = {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
                "application/pdf": "pdf",
                "image/png": "png",
            }.get(export_as, "bin")
            params = {
                "mimeType": export_as,
                "supportsAllDrives": "true",
            }
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(
                    f"https://www.googleapis.com/drive/v3/files/{target.id}/export",
                    params=params,
                    headers=headers,
                )
            if resp.status_code in (403, 404):
                raise PermissionError("Drive export forbidden; verify sharing and scope")
            resp.raise_for_status()
            data = resp.content
            filename = target.name if target.name.endswith(f".{extension}") else f"{target.name}.{extension}"
            return data, filename, export_as

        # Binary/object download
        params = {"alt": "media", "supportsAllDrives": "true"}
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{target.id}",
                params=params,
                headers=headers,
            )
        if resp.status_code in (403, 404):
            logger.error(
                "drive download_file %s status=%s body=%s resource_key_len=%s",
                target.id,
                resp.status_code,
                resp.text,
                len(target.resource_key) if target.resource_key else 0,
            )
            detail = resp.json().get("error", {}).get("message") if resp.headers.get("content-type", "").startswith("application/json") else resp.text
            raise PermissionError(f"Drive download forbidden; verify sharing and scope. Google said: {detail}")
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "application/octet-stream")
        return resp.content, target.name, content_type


def is_drive_url(url: str) -> bool:
    return parse_drive_url(url) is not None


def export_mime_for(mime_type: str) -> Optional[str]:
    """Return the preferred export MIME for a Google Docs editor type."""
    return _EXPORT_MIME_MAP.get(mime_type)
