"""The ServiceUnavailable failure a client reports."""

from __future__ import annotations

from src.ui.client.service_client_error import ServiceClientError


class ServiceUnavailable(ServiceClientError):
    """No service answered, or none could be started.

    ``launch_code`` carries the launcher's own identifier when the failure came
    from `src/server/launch.py` — ``service_starting`` means "retry in a moment",
    ``service_died`` means "the start-up failed, read the message" — so the screen
    can say which of the two it is without matching text.
    """

    code = 'service_unavailable'

    def __init__(self, message: str, *, launch_code: str | None = None) -> None:
        super().__init__(message)
        self.launch_code = launch_code
