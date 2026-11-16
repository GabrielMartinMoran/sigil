"""The SaveResult value used by the session store."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SaveResult:
    """Which session row a checkpoint wrote.

    ``session_id`` is the logical session; when a recovery copy was written,
    ``recovery_session_id`` names it and ``destination_id`` is the session whose
    transcript received any confirmed deliveries.
    """

    session_id: str
    recovery_session_id: str | None = None

    @property
    def destination_id(self) -> str:
        return self.recovery_session_id or self.session_id
