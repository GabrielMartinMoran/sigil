"""The NoHistoryYet failure a client reports."""

from __future__ import annotations

from src.ui.service_client_error import ServiceClientError


class NoHistoryYet(ServiceClientError):
    """The service has no durable row for this session yet: nothing has been said.

    A conversation that was never saved has no transcript to paint, and that is a
    normal state, not a failure. The screen shows an empty transcript and keeps
    listening; a sentence about an unreadable reply would be a lie about a service
    that answered a correct 404.
    """

    code = 'no_history_yet'
