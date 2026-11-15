"""The Unauthorized failure a client reports."""

from __future__ import annotations

from src.ui.service_client_error import ServiceClientError


class Unauthorized(ServiceClientError):
    """The run token was refused: this client is talking to another run."""

    code = 'unauthorized'
