"""The UnexpectedResponse failure a client reports."""

from __future__ import annotations

from src.ui.service_client_error import ServiceClientError


class UnexpectedResponse(ServiceClientError):
    """The reply is not the shape this client knows: not JSON, or a status it cannot name."""

    code = 'unexpected_response'
