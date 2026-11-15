"""Session titles: naming a conversation and putting the name in the window.

A title is a label, so these tests are about the two things a label can get wrong:
what it says (a model answer is untrusted text, not a title) and when it is
painted (the window must describe the session that is actually open).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from src.core.sessions import titling
from src.core.sessions.session_store import Session, SessionStore
from src.core.sessions.titling import TITLE_INSTRUCTION, clean_title, first_user_message, generate_title, shorten

# --- helpers ----------------------------------------------------------------


def _request_text(messages: list[ModelMessage]) -> str:
    """Everything the model was sent, as one string, for matching a request kind."""
    parts: list[str] = []
    for message in messages:
        for part in getattr(message, 'parts', ()):
            content = getattr(part, 'content', '')
            if isinstance(content, str):
                parts.append(content)
    return '\n'.join(parts)


def _answer(title: str, record: list[list] | None = None, *, refuse_naming: bool = False):
    """A model function that answers ``title``, and records what it was asked.

    The title request and the turn share one model (that is the point: naming is
    free for the user), so the function tells them apart by the instruction.
    """

    def model_function(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if record is not None:
            record.append(list(messages))
        if refuse_naming and TITLE_INSTRUCTION in _request_text(messages):
            raise RuntimeError('the provider said no')
        return ModelResponse(parts=[TextPart(title)])

    return model_function


@pytest.fixture(autouse=True)
def _restore_cwd():
    """Switching workspace `chdir`s, and it is global.

    These tests switch into a temporary directory that the test then deletes: the
    rest of the suite would start in a directory that no longer exists, and every
    subprocess it spawns would fail on `getcwd`.
    """
    original = os.getcwd()
    yield
    os.chdir(original)


# --- clean_title: the answer is text from a model, not a title --------------


@pytest.mark.parametrize(
    'raw',
    [
        'Fix the login form',
        '"Fix the login form"',
        "'Fix the login form'",
        'Title: Fix the login form',
        'Session title: Fix the login form',
        'Título: Fix the login form',
        '**Fix the login form**',
        'Fix the login form.',
        '  Fix   the   login   form  ',
        'Fix the login form\n',
    ],
)
def test_a_decorated_answer_cleans_to_the_same_title(raw: str) -> None:
    assert clean_title(raw) == 'Fix the login form'


def test_a_title_keeps_the_language_of_the_session() -> None:
    assert clean_title('Arreglar el formulario de acceso') == 'Arreglar el formulario de acceso'


def test_an_empty_answer_is_not_a_title() -> None:
    assert clean_title('') == ''
    assert clean_title('   \n\t ') == ''


def test_a_long_title_is_cut_at_a_word_boundary() -> None:
    title = clean_title('word ' * 40)
    assert len(title) <= titling.TITLE_MAX_LENGTH
    assert title.endswith('…')
    assert 'wor…' not in title


def test_shorten_never_exceeds_the_limit() -> None:
    for length in (0, 1, 10, titling.TITLE_MAX_LENGTH, titling.TITLE_MAX_LENGTH + 1, 500):
        assert len(shorten('a ' * length)) <= titling.TITLE_MAX_LENGTH


def test_shorten_collapses_a_multi_line_message_to_one_line() -> None:
    assert shorten('first line\n\nsecond   line') == 'first line second line'


# --- first_user_message: what the naming request reads ---------------------


def test_the_first_user_message_is_the_source_of_the_title() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content='the first one')]),
        ModelRequest(parts=[UserPromptPart(content='the second one')]),
    ]
    assert first_user_message(messages) == 'the first one'


def test_an_empty_history_has_no_source() -> None:
    assert first_user_message([]) == ''


def test_a_message_without_text_is_skipped() -> None:
    """An image-only message says nothing a title could be made of."""
    messages = [ModelRequest(parts=[]), ModelRequest(parts=[UserPromptPart(content='the text')])]
    assert first_user_message(messages) == 'the text'


# --- generate_title: one request, no tools, no history ---------------------


def test_the_request_carries_the_instruction_and_the_message_and_nothing_else() -> None:
    seen: list[list[ModelMessage]] = []

    title = asyncio.run(generate_title(FunctionModel(_answer('Fix the login form', seen)), 'the login form is broken'))

    assert title == 'Fix the login form'
    request = seen[0]
    # Two messages and no more: the instruction, then the message as data. No
    # session history, so the request cannot be mistaken for a turn.
    assert len(request) == 2
    assert TITLE_INSTRUCTION in _request_text(request)
    assert 'the login form is broken' in _request_text(request)
    assert TITLE_INSTRUCTION not in 'the login form is broken'


def test_a_very_long_message_is_trimmed_before_it_is_sent() -> None:
    seen: list[list[ModelMessage]] = []

    asyncio.run(generate_title(FunctionModel(_answer('A title', seen)), 'x' * 10_000))

    assert len(seen[0][-1].parts[0].content) == titling.SOURCE_MAX_LENGTH


def test_naming_asks_for_reasoning_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """A label does not need a reasoning pause in front of the first turn.

    The setting is asserted where Sigil sends it: `FunctionModel` drops keys its
    profile does not support, which would make the check about pydantic-ai rather
    than about the request Sigil builds.
    """
    seen: list[dict] = []

    async def fake_request(model, messages, *, model_settings=None, model_request_parameters=None, instrument=None):
        seen.append(dict(model_settings or {}))
        return ModelResponse(parts=[TextPart('A title')])

    monkeypatch.setattr(titling, 'model_request', fake_request)

    title = asyncio.run(generate_title(TestModel(), 'do something'))

    assert title == 'A title'
    assert seen[0]['thinking'] is False
    assert seen[0]['max_tokens'] == titling.TITLE_MAX_TOKENS


def test_nothing_to_name_costs_no_request() -> None:
    seen: list[list[ModelMessage]] = []

    model = FunctionModel(_answer('unused', seen))
    assert asyncio.run(generate_title(model, '   ')) == ''
    assert asyncio.run(generate_title(None, 'a message')) == ''
    assert seen == []


# --- Session.display_title: the fallback ------------------------------------


def test_an_unnamed_session_falls_back_to_its_first_message() -> None:
    session = Session(id='a' * 32, cwd='/tmp')
    session.messages = [ModelRequest(parts=[UserPromptPart(content='Try the new parser')])]
    assert session.display_title == 'Try the new parser'


def test_a_named_session_uses_its_title() -> None:
    session = Session(id='a' * 32, cwd='/tmp', title='New parser')
    session.messages = [ModelRequest(parts=[UserPromptPart(content='Try the new parser')])]
    assert session.display_title == 'New parser'


def test_a_new_session_has_no_title_and_no_crash() -> None:
    assert Session(id='a' * 32, cwd='/tmp').display_title == ''


def test_the_fallback_is_cut_to_the_same_length_as_a_title() -> None:
    session = Session(id='a' * 32, cwd='/tmp')
    session.messages = [ModelRequest(parts=[UserPromptPart(content='word ' * 40)])]
    assert len(session.display_title) <= titling.TITLE_MAX_LENGTH


def test_the_title_survives_a_restart(tmp_path: Path) -> None:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
    session = store.create_session()
    session.title = 'Fix the login form'
    store.save(session)

    assert store.load(session.id).display_title == 'Fix the login form'


def test_naming_goes_through_the_router_the_agent_holds() -> None:
    """The title costs the account the session already uses; no second provider.

    The agent holds the ``ModelRouter`` in production, so this is the path that
    actually runs: the title has to reach whichever model the router points at now.
    """
    from src.core.providers.factory.registry import ModelRouter

    router = ModelRouter(FunctionModel(_answer('A first name')))
    assert asyncio.run(generate_title(router, 'the login form is broken')) == 'A first name'

    # A switch between turns reaches the naming request too: it reads the model
    # the user is on now, not the one the router started with.
    router.use(FunctionModel(_answer('Fix the login form')))
    assert asyncio.run(generate_title(router, 'the login form is broken')) == 'Fix the login form'
