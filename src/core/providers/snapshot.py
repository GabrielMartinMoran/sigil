"""What the UI needs to paint the model state, in one immutable object.

The side panel gets this, never the service or the stores. That keeps the
widgets free of side effects and makes them trivially testable.
"""

from __future__ import annotations

from src.core.providers.model_snapshot import ModelSnapshot

EMPTY_SNAPSHOT = ModelSnapshot()
"""What the app shows before anything is configured."""
