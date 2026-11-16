"""The new_installation_id function of the storage layer."""

from __future__ import annotations

import uuid


def new_installation_id() -> str:
    """A stable, opaque installation identifier in the form OpenAI accepts."""
    return uuid.uuid4().urn
