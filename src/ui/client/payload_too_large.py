"""The PayloadTooLarge failure a client reports."""

from __future__ import annotations

from src.ui.client.service_client_error import ServiceClientError


class PayloadTooLarge(ServiceClientError):
    """The body passed the service's command ceiling and was refused unread."""

    code = 'too_large'
