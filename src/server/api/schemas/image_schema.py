"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ImageSchema(BaseModel):
    """One image of a message, in the order the text marks it.

    The shape is deliberately the bytes and nothing else: ``media_type`` is what the
    picture claims to be and ``data`` is its base64 payload. There is no filename
    field, because a client that sent a path would be asking the server to open a
    file, and the API never does that.
    """

    media_type: str = Field(min_length=1)
    data: str = Field(min_length=1)
