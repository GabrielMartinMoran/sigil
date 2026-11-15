"""Naming a session after what the user asked it for.

A session id is opaque and the first message is a poor label: it is written for
the agent to act on, not for a reader to recognize, so it is often long and
always unedited. The session therefore keeps a *title*, generated once from that
first message by the same model the conversation runs on: the message goes back
with a one-line instruction and the answer becomes the label shown in the window
title and in `/load`.

The title is decoration, never a dependency. An empty answer, a provider error
or a session that is closed mid-request all leave the session unnamed, and the
listing falls back to the first message cut to the same length
(``Session.display_title``). Nothing here raises to the caller for that reason:
losing a title is not losing a conversation.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.direct import model_request
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    SystemPromptPart,
    UserPromptPart,
)
from pydantic_ai.models import Model

TITLE_MAX_LENGTH = 60
"""Long enough to say what the session is about, short enough for a window title."""

SOURCE_MAX_LENGTH = 2000
"""Characters of the first message the titling request reads. A title needs the gist."""

TITLE_MAX_TOKENS = 512
"""Room for a reasoning model to think before it writes one line."""

TITLE_INSTRUCTION = (
    'You name conversations. Read the message and answer with a title for it: one line, '
    f'at most {TITLE_MAX_LENGTH} characters, in the language of the message. Say what the '
    'session works on, do not answer it, and do not mention that you are naming anything. '
    'No quotes, no markdown, no final period.'
)
"""The whole prompt. The message itself goes in a request of its own, unedited."""

_LABEL_PREFIXES = (
    'title:',
    'session title:',
    'conversation title:',
    'título:',
    'titulo:',
)
"""Labels a model sometimes puts in front of the answer, in either language."""

_EDGE_CHARACTERS = ' \t\r\n"\'`*_#-–—'
"""Decoration a title does not want at either end: quotes, markdown, dashes."""


def first_user_message(messages: Sequence[ModelMessage]) -> str:
    """The first text the user sent, or an empty string.

    Only plain text counts: a message that carries images has its caption here
    and the images elsewhere, and a message with no text at all is not a label.
    """
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                text = ' '.join(part.content.split())
                if text:
                    return text
    return ''


def shorten(text: str, limit: int = TITLE_MAX_LENGTH) -> str:
    """``text`` on one line, cut at a word boundary when it is longer than ``limit``.

    The ellipsis is part of the budget, so the result never exceeds ``limit``
    characters, which is what lets the window title and the picker share a width.
    """
    collapsed = ' '.join(str(text or '').split())
    if len(collapsed) <= limit:
        return collapsed

    cut = collapsed[: limit - 1].rstrip()
    space = cut.rfind(' ')
    if space > (limit - 1) // 2:
        cut = cut[:space].rstrip()
    return cut.rstrip(' ,.;:-–—') + '…'


def clean_title(raw: str, limit: int = TITLE_MAX_LENGTH) -> str:
    """The usable title in a model answer.

    Models decorate: quotes around the answer, a ``Title:`` label, a closing
    period, a line break in the middle. All of it is noise in a window title, and
    stripping it here keeps every caller free of the same cleanup.
    """
    text = ' '.join(str(raw or '').split())
    if not text:
        return ''

    text = text.strip(_EDGE_CHARACTERS)
    lowered = text.lower()
    for prefix in _LABEL_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :].strip(_EDGE_CHARACTERS)
            break
    if text.endswith('.') and not text.endswith('..'):
        text = text[:-1].rstrip(_EDGE_CHARACTERS)

    return shorten(text, limit)


async def generate_title(model: Model | None, first_message: str) -> str:
    """Asks ``model`` for a title for a conversation that starts with ``first_message``.

    The request is deliberately not part of the agent's history: it has no tools,
    no instructions of the session and no memory of the conversation it names, so
    it cannot be confused for a turn. It reads the message as data and answers
    one line.

    Args:
        model: The model the session runs on, so the title costs the account the
            user already configured and nothing else.
        first_message: What the user sent first.

    Returns:
        The title, cleaned; an empty string when there is nothing to name or the
        model answered nothing usable.
    """
    text = str(first_message or '').strip()
    if not text or model is None:
        return ''

    response = await model_request(
        model,
        [
            ModelRequest(parts=[SystemPromptPart(content=TITLE_INSTRUCTION)]),
            ModelRequest(parts=[UserPromptPart(content=text[:SOURCE_MAX_LENGTH])]),
        ],
        # Reasoning off, on purpose: naming a conversation is a reading task and
        # the cheapest level answers it just as well, without a pause in front of
        # the first title of every session.
        model_settings={'thinking': False, 'max_tokens': TITLE_MAX_TOKENS},
    )
    return clean_title(response.text)
