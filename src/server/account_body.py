"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AccountBody(BaseModel):
    """A provider account to add, with the credential it needs.

    ``auth`` is accepted so an OAuth request is answered explicitly instead of
    being misread as a key-less API-key account.
    """

    alias: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    api_key: str | None = None
    auth: str = 'api_key'
