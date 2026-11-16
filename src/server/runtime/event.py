"""One event of the server log, with the sequence a client resumes from."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Event:
    """One thing that happened, with the sequence number a client catches up from.

    ``session_id`` is internal ownership, never wire data: ``to_sse`` and
    ``to_dict`` do not know it exists, and ``data`` may carry its own unrelated
    ``session_id`` field. ``None`` means the event belongs to the service.
    """

    seq: int
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None

    def to_sse(self) -> str:
        """The wire form: `id` for the browser, `event` for dispatch, `data` for JSON."""
        payload = {'seq': self.seq, 'type': self.type, **self.data}
        return f'id: {self.seq}\nevent: {self.type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'

    def to_dict(self) -> dict[str, Any]:
        return {'seq': self.seq, 'type': self.type, **self.data}

    def matches_session(self, session_id: str | None) -> bool:
        """Whether a consumer scoped to ``session_id`` should receive this event.

        ``None`` on the consumer side is internal all-events access. On the event
        side it means the event is service-wide, so every scope sees it. Neither
        side consults ``data``: a payload field never implies ownership.
        """
        if session_id is None or self.session_id is None:
            return True
        return self.session_id == session_id
