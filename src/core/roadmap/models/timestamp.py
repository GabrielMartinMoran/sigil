"""Timestamp formatting for roadmap items."""

from datetime import UTC, datetime


def now_iso() -> str:
    """Return the current UTC time in ISO 8601 format."""
    return datetime.now(UTC).isoformat()
