"""Request body for one API route."""

from __future__ import annotations

from pydantic import BaseModel, StrictBool


class ExternalFilesSchema(BaseModel):
    """Whether the session may read files outside its workspace.

    ``StrictBool`` on purpose: the terminal's `/external-files` refuses anything
    that is not `allow` or `deny`, and a lax bool would read `"yes"` as consent to
    a permission change nobody spelled out.
    """

    allow: StrictBool
