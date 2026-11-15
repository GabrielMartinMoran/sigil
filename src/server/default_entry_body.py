"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DefaultEntryBody(BaseModel):
    """One defaults entry: the account, the model and the level it starts on.

    The three keys are the file's own — `agents.<name>` and `default_model` hold
    exactly these — so a client writes the shape the reader accepts instead of a
    translation of it.
    """

    account: str = Field(min_length=1)
    model: str = Field(min_length=1)
    effort: str = 'default'
