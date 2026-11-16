"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RollbackSchema(BaseModel):
    """A rollback point to return to.

    ``files`` restores the workspace snapshot too; ``False`` puts the conversation
    back and leaves the tree as it is, which is the other choice the picker offers.
    """

    turn_id: str = Field(min_length=1)
    files: bool = True
