"""The StreamInterrupted failure a client reports."""

from __future__ import annotations

from src.ui.service_client_error import ServiceClientError


class StreamInterrupted(ServiceClientError):
    """The event stream ended without the client closing it.

    ``last_event_id`` is the cursor to reconnect from: passing it to
    `ServiceClient.events` asks the service for what was missed, which is the whole
    reason the stream carries sequence numbers.
    """

    code = 'stream_interrupted'

    def __init__(self, message: str, *, last_event_id: int | None) -> None:
        super().__init__(message)
        self.last_event_id = last_event_id
