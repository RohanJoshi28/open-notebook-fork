from typing import ClassVar, Optional

from pydantic import Field, field_validator

from open_notebook.domain.base import ObjectModel
from open_notebook.database.repository import ensure_record_id


class User(ObjectModel):
    """
    Application user, created from Google OIDC login.
    """

    table_name: ClassVar[str] = "user"

    email: str
    sub: str = Field(..., description="OIDC subject (Google user id)")
    name: Optional[str] = None
    picture: Optional[str] = None

    @field_validator("id", mode="before")
    @classmethod
    def normalize_ids(cls, value):
        if not value:
            return value
        return str(ensure_record_id(value))
