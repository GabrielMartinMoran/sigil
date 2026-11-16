"""The statuses this client can name, mapped to the error they raise."""

from __future__ import annotations

from src.ui.client.payload_too_large import PayloadTooLarge
from src.ui.client.request_rejected import RequestRejected
from src.ui.client.service_busy import ServiceBusy
from src.ui.client.unauthorized import Unauthorized

STATUS_ERRORS = {
    400: RequestRejected,
    401: Unauthorized,
    409: ServiceBusy,
    413: PayloadTooLarge,
}
