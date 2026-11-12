"""The agent in the UI: the status bar, `/agent`, `ctrl+a` and the session.

Four behaviours are pinned here, because each of them is a way the feature can
look finished and not be:

- the bar says which agent answers, and it survives a transient message;
- `ctrl+a` opens the picker with the focus in the prompt (it is `home` there);
- `/agent <name>` switches directly, and a name that does not exist is a notice,
  not a crash;
- the agent travels with the session the way the model does.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from rich.text import Text
from textual.widgets import OptionList

from src.engine.agents.profile.registry import AgentRegistry
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.agent_picker_screen import AgentPickerScreen
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.status_bar import StatusBar
from tests.ui_helpers import wait_until

FRONTMATTER = '---\nname: {name}\ndescription: {description}\n{extra}---\n{body}\n'


def _write(root: Path, name: str, *, description: str = 'An agent.', body: str = 'Body.', extra: str = '') -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f'{name}.md').write_text(
        FRONTMATTER.format(name=name, description=description, body=body, extra=extra),
        encoding='utf-8',
    )


def _app(tmp: str, names: tuple[str, ...] = ('wizard', 'reviewer')) -> SigilApp:
    agents_root = Path(tmp) / 'agents'
    for name in names:
        _write(agents_root, name)
    registry = AgentRegistry([('project', agents_root)], active=names[0] if names else None)

    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    session = store.create_session()
    session.agent = registry.active_name
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=session,
        agent_registry=registry,
    )


def _bar(app: SigilApp) -> str:
    return str(app.query_one(StatusBar).render())


def _chat(app: SigilApp) -> list[str]:
    texts: list[str] = []
    for widget in app.query_one(ChatLog).children:
        rendered = widget.render()
        plain = getattr(rendered, 'plain', None)
        texts.append(plain if isinstance(plain, str) else str(rendered))
    return texts


# --- the status bar ----------------------------------------------------------


def test_the_bar_starts_with_the_agent_in_effect():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)):
                assert 'Wizard' in _bar(app)

    asyncio.run(inner())


def test_a_transient_message_does_not_erase_the_agent():
    """The two halves are independent: Esc asks for attention, the agent stays."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                app._paint_status('Stopping…', style='yellow')
                await pilot.pause()
                assert 'Wizard' in _bar(app)
                assert 'Stopping' in _bar(app)

                app._paint_status('')
                await pilot.pause()
                assert 'Wizard' in _bar(app)

    asyncio.run(inner())


def test_the_bar_without_an_agent_paints_only_the_activity():
    """`_left()` directly: `render()` needs a layout to place the right group."""
    bar = StatusBar()
    assert bar._left().plain == '● Ready'
    bar._message = Text('Esc again')
    assert bar._left().plain == 'Esc again'


# --- the picker --------------------------------------------------------------


def test_ctrl_a_opens_the_agent_picker_from_the_prompt():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                app.query_one(PromptInput).focus()
                await pilot.press('ctrl+a')
                await wait_until(pilot, lambda: isinstance(app.screen, AgentPickerScreen))
                assert isinstance(app.screen, AgentPickerScreen)

    asyncio.run(inner())


def test_ctrl_a_does_not_move_the_cursor_instead():
    """`ctrl+a` is `home` in the `Input`: the picker has to win the key."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                field = app.query_one(PromptInput)
                field.focus()
                field.value = 'hola'
                field.cursor_position = len(field.value)
                await pilot.press('ctrl+a')
                await wait_until(pilot, lambda: isinstance(app.screen, AgentPickerScreen))
                assert field.value == 'hola'
                assert isinstance(app.screen, AgentPickerScreen)

    asyncio.run(inner())


def test_the_picker_lists_the_profiles_and_marks_the_current_one():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.press('ctrl+a')
                await wait_until(pilot, lambda: isinstance(app.screen, AgentPickerScreen))

                screen = app.screen
                options = screen.query_one(OptionList)
                assert [str(option.id) for option in options.options] == ['reviewer', 'wizard']
                assert str(options.highlighted_option.id) == 'wizard'
                # A row says what the profile can do, not only its name.
                assert 'all tools' in str(options.get_option_at_index(0).prompt)

    asyncio.run(inner())


def test_choosing_in_the_picker_switches_the_agent():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.press('ctrl+a')
                await wait_until(pilot, lambda: isinstance(app.screen, AgentPickerScreen))
                # The highlight starts on the active one; move up to `reviewer`.
                await pilot.press('up')
                await pilot.press('enter')
                await wait_until(pilot, lambda: app.active_agent().name == 'reviewer')

                assert not isinstance(app.screen, AgentPickerScreen)
                assert app.active_agent().name == 'reviewer'
                assert app._session.agent == 'reviewer'
                assert 'Reviewer' in _bar(app)

    asyncio.run(inner())


def test_the_picker_filters_as_you_type():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, names=('wizard', 'reviewer', 'writer'))
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.press('ctrl+a')
                await wait_until(pilot, lambda: isinstance(app.screen, AgentPickerScreen))
                await pilot.press('w', 'r')
                options = app.screen.query_one(OptionList)
                assert [str(option.id) for option in options.options] == ['writer']

    asyncio.run(inner())


def test_escape_closes_the_picker_and_keeps_the_agent():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.press('ctrl+a')
                await wait_until(pilot, lambda: isinstance(app.screen, AgentPickerScreen))
                await pilot.press('escape')
                await wait_until(pilot, lambda: not isinstance(app.screen, AgentPickerScreen))
                assert not isinstance(app.screen, AgentPickerScreen)
                assert app.active_agent().name == 'wizard'

    asyncio.run(inner())


# --- the command -------------------------------------------------------------


def test_the_command_switches_directly():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                app.query_one(PromptInput).value = '/agent reviewer'
                await pilot.press('enter')
                await wait_until(pilot, lambda: any('Agent: Reviewer' in text for text in _chat(app)))

                assert app.active_agent().name == 'reviewer'
                assert any('Agent: Reviewer' in text for text in _chat(app))

    asyncio.run(inner())


def test_an_unknown_name_is_a_notice_not_a_crash():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                app.query_one(PromptInput).value = '/agent nope'
                await pilot.press('enter')
                await wait_until(pilot, lambda: any('Unknown agent' in text for text in _chat(app)))

                assert app.active_agent().name == 'wizard'
                assert any('Unknown agent' in text for text in _chat(app))

    asyncio.run(inner())


def test_the_command_with_no_profiles_says_where_to_put_one():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, names=())
            async with app.run_test(size=(120, 40)) as pilot:
                app.query_one(PromptInput).value = '/agent'
                await pilot.press('enter')
                await wait_until(pilot, lambda: any('.sigil/agents' in text for text in _chat(app)))

                assert any('.sigil/agents' in text for text in _chat(app))

    asyncio.run(inner())


# --- the session -------------------------------------------------------------


def test_a_new_session_inherits_the_agent():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                app.query_one(PromptInput).value = '/agent reviewer'
                await pilot.press('enter')
                await wait_until(pilot, lambda: app.active_agent().name == 'reviewer')

                previous_session = app._session.id
                app.query_one(PromptInput).value = '/new'
                await pilot.press('enter')
                await wait_until(pilot, lambda: app._session.id != previous_session)

                assert app._session.agent == 'reviewer'
                assert 'Reviewer' in _bar(app)

    asyncio.run(inner())


def test_switching_agent_is_refused_while_a_turn_runs():
    """An agent swap changes the tool set of a run already in flight."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()
            app = _app(tmp)
            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            app._agent = agent
            async with app.run_test(size=(120, 40)) as pilot:
                app.start_turn('go')
                for _ in range(40):
                    await pilot.pause()
                    if app._current_run is not None:
                        break

                app.query_one(PromptInput).value = '/agent reviewer'
                await pilot.press('enter')
                await wait_until(
                    pilot, lambda: any('not available while the agent works' in text for text in _chat(app))
                )

                assert app.active_agent().name == 'wizard'
                assert any('not available while the agent works' in text for text in _chat(app))
                release.set()
                for _ in range(40):
                    await pilot.pause()
                    if not app._turn_active:
                        break

    asyncio.run(inner())


def test_the_agent_survives_being_saved(tmp_path):
    store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')
    session = store.create_session()
    session.agent = 'reviewer'
    store.save(session)

    assert store.load(session.id).agent == 'reviewer'


def test_a_session_without_a_profile_reads_back_empty(tmp_path):
    """An empty profile means "no choice recorded": the registry's default applies."""
    store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')
    session = store.create_session()
    store.database.execute(
        "UPDATE agent_executions SET profile = '', name = '' WHERE session_id = ?",
        (session.id,),
    )

    assert store.load(session.id).agent == ''


def test_selecting_agent_applies_its_model_but_binding_session_does_not(tmp_path):
    import asyncio

    app = _app(str(tmp_path))
    selected = []
    app._on_agent_selected = lambda name: selected.append(name)

    async def run():
        async with app.run_test(size=(120, 40)):
            app.apply_agent('reviewer', app.query_one(ChatLog))
            assert selected == ['reviewer']
            app.bind_agent('wizard')
            assert selected == ['reviewer']

    asyncio.run(run())


def test_new_session_binds_the_inherited_agent_so_its_model_resolves(tmp_path):
    """`/new` inherits the agent, and binding is where the model is resolved.

    The profile travels with the session and the composition applies that profile's
    model — or `default_model`, or the last one used — when the session is bound. The
    app no longer reaches for the agent's model on its own: one seam, one answer.
    """
    app = _app(str(tmp_path))
    bound: list[tuple[str, object]] = []
    app._on_bind_session = lambda session: bound.append((session.agent, session.selection))

    async def run():
        async with app.run_test(size=(120, 40)):
            await app.new_session(app.query_one(ChatLog))
            assert [agent for agent, _ in bound] == ['wizard']

    asyncio.run(run())
