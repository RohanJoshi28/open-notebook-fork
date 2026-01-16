from typing import ClassVar, Optional

from pydantic import Field, field_validator

from open_notebook.domain.base import ObjectModel
from open_notebook.database.repository import ensure_record_id


class GoogleCredential(ObjectModel):
    """
    Stores OAuth tokens for a user's Google account (Drive access).
    """

    table_name: ClassVar[str] = "google_credential"

    user: str
    refresh_token: str
    access_token: Optional[str] = None
    expires_at: Optional[str] = None
    scope: Optional[str] = None
    token_type: Optional[str] = None

    @field_validator("user", mode="before")
    @classmethod
    def normalize_user(cls, value: str) -> str:
        return str(ensure_record_id(value))

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value):
        if not value:
            return value
        return str(ensure_record_id(value))
