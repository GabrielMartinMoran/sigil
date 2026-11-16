"""Session titles: naming a conversation and putting the name in the window.

A title is a label, so these tests are about the two things a label can get wrong:
what it says (a model answer is untrusted text, not a title) and when it is
painted (the window must describe the session that is actually open).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from textual.widgets import OptionList

from src.core.providers.factory.unconfigured_model import UnconfiguredModel
from src.core.sessions.catalog_entry import CatalogEntry
from src.core.sessions.session import Session
from src.core.sessions.session_store import SessionStore
from src.core.sessions.titling import TITLE_INSTRUCTION
from src.core.workspace.models.workspace import Workspace
from src.core.workspace.workspace_manager import WorkspaceManager
from src.ui import window_title
from src.ui.sigil_app import SigilApp
from src.ui.widgets.session_option import session_option
from src.ui.widgets.session_picker import SessionPicker

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


def _app(tmp: str, *, model=None, workspace_manager=None) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    options = {'workspace_manager': workspace_manager} if workspace_manager is not None else {}
    return SigilApp(
        agent=Agent(model or TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=store.create_session(),
        **options,
    )


async def _settle(pilot, predicate, attempts: int = 120) -> None:
    """Pauses the app until ``predicate`` holds, or gives it plenty of chances."""
    for _ in range(attempts):
        await pilot.pause()
        if predicate():
            return


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ['git', *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            'GIT_AUTHOR_NAME': 'Test',
            'GIT_AUTHOR_EMAIL': 'test@example.com',
            'GIT_COMMITTER_NAME': 'Test',
            'GIT_COMMITTER_EMAIL': 'test@example.com',
            'PATH': '/usr/bin:/bin:/usr/local/bin',
            'HOME': str(cwd),
        },
    )


# --- window_title: the sequence written to the terminal --------------------


def test_the_window_title_names_the_repository_and_the_session() -> None:
    assert window_title.compose('sigil', 'Fix the login form') == 'sigil — Fix the login form'


def test_each_half_is_shown_alone_when_the_other_is_missing() -> None:
    assert window_title.compose('sigil', '') == 'sigil'
    assert window_title.compose('', 'Fix the login form') == 'Fix the login form'
    assert window_title.compose('', '') == ''


def test_a_title_from_a_model_cannot_inject_a_control_sequence() -> None:
    """`OSC` ends on BEL: a stray one would hand the rest of the title to the terminal."""
    composed = window_title.compose('sigil', 'evil\x07\x1b]0;pwned\x07 title')

    assert '\x07' not in composed
    assert '\x1b' not in composed
    assert 'evil' in composed and 'pwned' in composed


def test_the_window_title_is_cut_to_a_terminal_friendly_length() -> None:
    assert len(window_title.compose('sigil', 'x' * 400)) <= window_title.MAX_LENGTH


class _RecordingDriver:
    """A driver that records what was written, like the one the app writes to."""

    def __init__(self, *, is_headless: bool = False) -> None:
        self.writes: list[str] = []
        self.is_headless = is_headless

    def write(self, text: str) -> None:
        self.writes.append(text)


def test_applying_writes_one_osc_sequence() -> None:
    driver = _RecordingDriver()
    assert window_title.apply(driver, 'sigil — Fix the login form') is True
    assert driver.writes == ['\x1b]2;sigil — Fix the login form\x07']


def test_applying_without_a_driver_or_a_title_writes_nothing() -> None:
    assert window_title.apply(None, 'sigil') is False

    driver = _RecordingDriver()
    assert window_title.apply(driver, '') is False
    assert driver.writes == []


def test_a_headless_run_does_not_try_to_rename_a_window() -> None:
    driver = _RecordingDriver(is_headless=True)
    assert window_title.apply(driver, 'sigil') is False
    assert driver.writes == []


def test_a_driver_that_refuses_the_sequence_does_not_raise() -> None:
    class Broken(_RecordingDriver):
        def write(self, text: str) -> None:
            raise OSError('the terminal is gone')

    assert window_title.apply(Broken(), 'sigil') is False


# --- the app: naming on the first turn, repainting on every change ---------


def test_the_first_turn_names_the_session_and_the_window() -> None:
    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, model=FunctionModel(_answer('Fix the login form')))
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.start_turn('the login form is broken')
                await _settle(pilot, lambda: bool(app._session.title))

                assert app._session.title == 'Fix the login form'
                assert 'Fix the login form' in app._painted_title

    asyncio.run(inner())


def test_a_second_turn_does_not_rename_the_session() -> None:
    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seen: list[list[ModelMessage]] = []
            app = _app(tmp, model=FunctionModel(_answer('Fix the login form', seen)))
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.start_turn('the login form is broken')
                await _settle(pilot, lambda: bool(app._session.title))
                await _settle(pilot, lambda: not app._turn_active)

                app.start_turn('now the signup form')
                await _settle(pilot, lambda: not app._turn_active)

                namings = [message for message in seen if TITLE_INSTRUCTION in _request_text(message)]
                assert len(namings) == 1
                assert app._session.title == 'Fix the login form'

    asyncio.run(inner())


def test_a_steering_message_does_not_rename_anything() -> None:
    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seen: list[list[ModelMessage]] = []
            app = _app(tmp, model=FunctionModel(_answer('Fix the login form', seen)))
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.start_turn('the login form is broken')
                app.start_turn('actually, the signup form')
                await _settle(pilot, lambda: bool(app._session.title))

                namings = [message for message in seen if TITLE_INSTRUCTION in _request_text(message)]
                assert len(namings) == 1
                assert app._session.title == 'Fix the login form'

    asyncio.run(inner())


def test_an_empty_answer_leaves_the_session_on_its_first_message() -> None:
    """A model that answers nothing must not paint an empty window title."""

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, model=FunctionModel(_answer('   ')))
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                painted_before = app._painted_title
                app.start_turn('the login form is broken')
                await _settle(pilot, lambda: not app._turn_active)

                assert app._session.title is None
                assert app._painted_title == painted_before
                # The listing still says something useful.
                assert app._session.display_title == 'the login form is broken'

    asyncio.run(inner())


def test_a_failing_title_request_leaves_the_turn_alone() -> None:
    """Naming is decoration: a provider that refuses it cannot cost the turn."""

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, model=FunctionModel(_answer('ok', refuse_naming=True)))
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.start_turn('the login form is broken')
                await _settle(pilot, lambda: not app._turn_active)

                assert app._session.title is None
                assert app._session.messages, 'the turn itself still ran'

    asyncio.run(inner())


def test_a_session_without_a_provider_is_left_unnamed() -> None:
    """First run, no account: the refusal costs a title, not the app."""

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'unconfigured'
            root.mkdir()
            app = _app(str(root), model=UnconfiguredModel(), workspace_manager=WorkspaceManager(root))
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.name_session(app._session, 'the login form is broken')
                await _settle(pilot, lambda: app._naming_attempts.get(app._session.id, 0) > 0)
                await _settle(pilot, lambda: True)

                assert app._session.title is None
                # One attempt, and the window still says where it is.
                assert app._painted_title == 'unconfigured'

    asyncio.run(inner())


def test_two_fast_turns_spend_one_title_request() -> None:
    """The request is in flight, not answered: a second turn must not re-ask."""

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seen: list[list[ModelMessage]] = []
            app = _app(tmp, model=FunctionModel(_answer('Fix the login form', seen)))
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.name_session(app._session, 'the login form is broken')
                # Before the worker has run: the request is on its way.
                app.name_session(app._session, 'the login form is broken')
                await _settle(pilot, lambda: bool(app._session.title))

                namings = [message for message in seen if TITLE_INSTRUCTION in _request_text(message)]
                assert len(namings) == 1
                # And once it is answered, the session is no longer a candidate.
                assert not app.should_name_session(app._session)

    asyncio.run(inner())


def test_an_interrupted_naming_leaves_the_app_usable() -> None:
    """Cancelling the naming worker is not an error the app has to survive loudly."""

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, model=FunctionModel(_answer('Fix the login form')))
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                app.name_session(app._session, 'the login form is broken')
                app.workers.cancel_group(app, 'session-title')
                # Cancelled is not failed: the session can still be named later.
                await _settle(pilot, lambda: app.should_name_session(app._session))
                assert app._session.title is None
                assert app.should_name_session(app._session)

    asyncio.run(inner())


def test_the_window_starts_on_the_repository_before_any_message() -> None:
    async def inner() -> None:
        root = Path(tmp) / 'my-project'
        root.mkdir(parents=True)
        _git(root, 'init', '-q')
        _git(root, 'commit', '-q', '--allow-empty', '-m', 'init')

        app = _app(str(root), workspace_manager=WorkspaceManager(root))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            assert app._painted_title == 'my-project'

    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(inner())


def test_a_linked_worktree_is_distinguishable_from_the_main_one() -> None:
    async def inner() -> None:
        root = Path(tmp) / 'my-project'
        root.mkdir(parents=True)
        _git(root, 'init', '-q')
        _git(root, 'commit', '-q', '--allow-empty', '-m', 'init')
        worktree = Path(tmp) / 'fix-login'
        _git(root, 'worktree', 'add', '-q', '-b', 'fix-login', str(worktree))

        manager = WorkspaceManager(root)
        manager.switch(str(worktree))
        app = _app(str(worktree), workspace_manager=manager)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            assert app._painted_title == 'my-project:fix-login'

    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(inner())


def test_out_of_a_repository_the_folder_names_the_window() -> None:
    async def inner() -> None:
        root = Path(tmp) / 'scratch'
        root.mkdir()
        app = _app(str(root), workspace_manager=WorkspaceManager(root))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            assert app._painted_title == 'scratch'

    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(inner())


def test_switching_workspace_repaints_the_window_title() -> None:
    async def inner() -> None:
        plain = Path(tmp) / 'plain'
        plain.mkdir()
        other = Path(tmp) / 'other-project'
        other.mkdir()
        _git(other, 'init', '-q')

        app = _app(str(plain), workspace_manager=WorkspaceManager(plain))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            assert app._painted_title == 'plain'

            app.switch_workspace(str(other))
            await pilot.pause()
            assert app._painted_title == 'other-project'

    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(inner())


def test_another_session_is_another_window_title() -> None:
    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                named = app._session
                named.title = 'The first conversation'
                app._store.save(named)
                app.bind_session(named)
                assert 'The first conversation' in app._painted_title

                app.bind_session(app._store.create_session())
                assert 'The first conversation' not in app._painted_title

    asyncio.run(inner())


def test_a_title_that_arrives_after_a_switch_does_not_repaint_the_window() -> None:
    """The title belongs to the session it names, not to the one now open."""

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                old = app._session
                app.bind_session(app._store.create_session())
                painted = app._painted_title

                old.title = 'The old conversation'
                await app.generate_session_title(old, 'the old message').wait()
                assert app._painted_title == painted

    asyncio.run(inner())


# --- the picker: the title instead of the raw first message ----------------


def _entry(session: Session, *, current: bool = False) -> CatalogEntry:
    return CatalogEntry(session=session, workspace=Workspace(root=Path('/repo')), is_current=current)


def _row_text(entry: CatalogEntry) -> str:
    return session_option(entry).prompt.plain


def test_the_picker_starts_with_the_local_last_updated_date() -> None:
    from datetime import datetime

    updated = '2026-11-09T13:42:00+00:00'
    session = Session(id='a' * 32, cwd='/repo', updated_at=updated)
    expected = datetime.fromisoformat(updated).astimezone().strftime('%Y-%m-%d %H:%M')
    assert _row_text(_entry(session, current=True)).startswith(f'{expected}  ● ')


def test_the_picker_handles_an_invalid_update_date() -> None:
    session = Session(id='a' * 32, cwd='/repo', updated_at='invalid')
    assert _row_text(_entry(session)).startswith('unknown date')


def test_the_picker_shows_the_generated_title() -> None:
    session = Session(id='a' * 32, cwd='/repo', title='Fix the login form')
    session.messages = [ModelRequest(parts=[UserPromptPart(content='the login form is broken and ')])]
    assert 'Fix the login form' in _row_text(_entry(session))


def test_an_unnamed_session_is_listed_by_its_first_message() -> None:
    session = Session(id='a' * 32, cwd='/repo')
    session.messages = [ModelRequest(parts=[UserPromptPart(content='Try the new parser')])]
    assert 'Try the new parser' in _row_text(_entry(session))


def test_an_empty_session_is_listed_as_empty() -> None:
    assert '(empty)' in _row_text(_entry(Session(id='a' * 32, cwd='/repo')))


def test_the_row_keeps_one_line_whatever_the_session_says() -> None:
    session = Session(id='a' * 32, cwd='/repo')
    session.messages = [ModelRequest(parts=[UserPromptPart(content='word ' * 200)])]
    row = _row_text(_entry(session))
    assert '\n' not in row
    assert row.count('word') <= 13


def test_the_picker_still_highlights_the_current_session() -> None:
    entries = [
        _entry(Session(id='a' * 32, cwd='/repo', title='First')),
        _entry(Session(id='b' * 32, cwd='/repo', title='Second'), current=True),
    ]

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            screen = SessionPicker(entries)
            async with app.run_test(size=(100, 30)) as pilot:
                app.push_screen(screen)
                await pilot.pause()
                highlighted = screen.query_one(OptionList).highlighted_option
                assert highlighted is not None
                assert str(highlighted.id) == 'b' * 32

    asyncio.run(inner())
