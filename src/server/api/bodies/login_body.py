"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginBody(BaseModel):
    """The only thing the login form sends."""

    password: str = Field(min_length=1)
