"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModelSchema(BaseModel):
    """A model to switch the session to.

    ``thinking`` is optional: omitting it keeps the level the model would pick by
    default, which is what the terminal's picker does when it only chooses a model.
    """

    account: str = Field(min_length=1)
    model: str = Field(min_length=1)
    thinking: str | None = None
