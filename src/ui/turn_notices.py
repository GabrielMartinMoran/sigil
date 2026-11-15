"""Helpers that used to live in turn_mixin."""

from __future__ import annotations

from src.ui.outgoing_image import OutgoingImage
from src.ui.payload_too_large import PayloadTooLarge
from src.ui.request_rejected import RequestRejected
from src.ui.service_busy import ServiceBusy
from src.ui.service_client_error import ServiceClientError
from src.ui.service_unavailable import ServiceUnavailable
from src.ui.turn_request import TurnRequest
from src.ui.unauthorized import Unauthorized
from src.ui.unexpected_response import UnexpectedResponse

_BACK_IN_PROMPT = ' What you typed is back in the prompt.'


_BUSY_MARKERS = ('turn is running', 'turn is in progress', 'a turn is running')


def _is_busy_reason(reason: str) -> bool:
    """Whether a `409` sentence is the service saying a turn is in the way."""
    text = reason.lower()
    return any(marker in text for marker in _BUSY_MARKERS)


def pending_request(previous: TurnRequest | None, text: str, images: tuple[OutgoingImage, ...]) -> TurnRequest:
    """The request to put on the wire: the failed one again, or a new message.

    The durable queue deduplicates by request id, so the id belongs to the content and
    not to the attempt. Sending the same text and images after a refusal is the same
    message and reuses its id; changing either is a different message and must not,
    because one id for two bodies is a conflict the service refuses. The decision is
    made here, once, and nothing on the server second-guesses it.
    """
    if previous is not None and previous.text == text and previous.images == images:
        return previous
    return TurnRequest(text=text, images=images)


def failure_notice(exc: ServiceClientError) -> str:
    """What the screen says about a refusal: the reason, then the way out.

    One function, because the difference between these failures is the whole reason
    the client raises typed ones: a terminal that printed them all the same way would
    have paid for the vocabulary and thrown it away. No sentence carries a traceback,
    and none stops at the reason — a person who cannot act on the message has learned
    nothing they can use.
    """
    if isinstance(exc, ServiceBusy):
        reason = str(exc).strip()
        if reason and not _is_busy_reason(reason):
            # Not every `409` means a turn is in the way: the routes use it for
            # anything the session cannot do *now*, including a turn it cannot
            # reach. Saying "still working" over that sentence would be inventing
            # a cause, so the server's own words are the answer.
            return f'The service refused this: {reason}'
        detail = f' The service said: {reason}' if reason else ''
        return (
            f'The agent is still working on this session.{detail} Wait for the turn to finish, or press Esc '
            'twice to stop it. What you typed is back in the prompt.'
        )
    if isinstance(exc, Unauthorized):
        return (
            'The service refused this terminal: its run token is not the one this service published. '
            'Restart Sigil so it attaches to the running service.'
        )
    if isinstance(exc, PayloadTooLarge):
        return (
            'That message is over the size the service accepts, images included. Remove an image or shorten '
            'the text, and send it again.'
        )
    if isinstance(exc, RequestRejected):
        return f'The service refused this message: {exc}'
    if isinstance(exc, ServiceUnavailable):
        if exc.launch_code == 'service_starting':
            return 'The service is still starting. Try again in a moment.' + _BACK_IN_PROMPT
        # The exception already opens with "No Sigil service answered at …": repeating
        # it here read as two failures instead of one.
        return f'{exc} Start it with `sigil server`, or check that it is still running.' + _BACK_IN_PROMPT
    if isinstance(exc, UnexpectedResponse):
        return (
            'The service answered something this terminal cannot read. Restart Sigil; if it happens again, '
            'the terminal and the service are different versions.'
        )
    return f'The service could not take this message: {exc}'
