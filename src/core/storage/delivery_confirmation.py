"""The DeliveryConfirmation value of the accepted-input queue."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeliveryConfirmation:
    """The facts that must agree before an input counts as delivered.

    The session and request name the acceptance; the attempt and run are the ones
    that actually carried the message. A missing identifier is not a delivery, and
    passing them explicitly keeps the checkpoint from confirming a whole session by
    coincidence.
    """

    session_id: str
    request_id: str
    attempt_id: str
    library_run_id: str

    def __post_init__(self) -> None:
        if not self.session_id or not self.request_id or not self.attempt_id or not self.library_run_id:
            raise ValueError('A delivery confirmation needs a session, request, attempt and run')
