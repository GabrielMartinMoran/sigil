"""The CompactionReason value of the context layer."""

from __future__ import annotations

from enum import StrEnum


class CompactionReason(StrEnum):
    DETERMINISTIC = 'deterministic'
    """Old tool payloads and reasoning reduced in one batch, without a model call."""

    SUMMARY = 'summary'
    """An older span replaced in the view by a structured model-written checkpoint."""

    EMERGENCY = 'emergency'
    """The projection still does not fit the hard safety budget."""
