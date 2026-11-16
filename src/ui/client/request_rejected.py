"""The RequestRejected failure a client reports."""

from __future__ import annotations

from src.ui.client.service_client_error import ServiceClientError


class RequestRejected(ServiceClientError):
    """The request names something the service will not accept; a different one would."""

    code = 'rejected'
