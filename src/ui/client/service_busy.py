"""The ServiceBusy failure a client reports."""

from __future__ import annotations

from src.ui.client.service_client_error import ServiceClientError


class ServiceBusy(ServiceClientError):
    """The request was well formed and cannot be applied while the session is busy."""

    code = 'busy'
