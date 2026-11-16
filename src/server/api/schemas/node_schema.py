"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class NodeSchema(BaseModel):
    """A node of the session tree, by id or a unique prefix of it."""

    node_id: str = Field(min_length=1)
