"""The prompt stays usable while the agent works.

Two behaviours are tested here, and they share one cause: a turn no longer takes
the keyboard hostage.

- A message typed mid-turn is **queued** and injected into the running agent at
  the next seam, so the model reads it before deciding what to do next. It is not
  a second turn competing with the first one.
- `Esc` asks before stopping: the first press writes the question in the status
  bar, the second one inside three seconds cancels the run and kills the shell
  commands it spawned. Anything else pressed in between calls it off.
"""

import asyncio
import os
import tempfile
import time
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from textual.widgets import Input

from src.engine.sessions.session_store import SessionStore
from src.engine.shell import running as running_commands
from src.engine.shell.runner import execute_command
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import AssistantBlock, ChatLog
from src.ui.widgets.command_menu import CommandMenu
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.status_bar import StatusBar


def _make_app(tmp: str, agent: Agent) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    return SigilApp(agent=agent, store=store, session=store.create_session())


async def _wait_for(pilot, predicate, attempts: int = 60) -> bool:
    for _ in range(attempts):
        await pilot.pause()
        if predicate():
            return True
    return False


def _status(app: SigilApp) -> str:
    return str(app.query_one(StatusBar).render())


def _chat_texts(app: SigilApp) -> list[str]:
    """Everything the chat shows, model blocks included."""
    texts: list[str] = []
    for widget in app.query_one(ChatLog).children:
        if isinstance(widget, AssistantBlock):
            texts.append(widget._text)
            continue
        rendered = widget.render()
        plain = getattr(rendered, 'plain', None)
        texts.append(plain if isinstance(plain, str) else str(rendered))
    return texts


# --- steering --------------------------------------------------------------


def test_a_message_typed_mid_turn_is_injected_into_the_running_agent():
    """The queued message reaches the model before it decides what comes next."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            seen: list[list[str]] = []
            release = asyncio.Event()
            started = asyncio.Event()

            agent = Agent(TestModel(call_tools=['wait_for_it'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def wait_for_it() -> str:
                started.set()
                await release.wait()
                return 'released'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('first')
                assert await _wait_for(pilot, started.is_set), 'the tool never started'
                # The turn is in flight and the prompt is not blocked.
                assert app.query_one(PromptInput).disabled is False
                assert app._turn_active is True

                # A message typed right here, while the tool is still running.
                await pilot.press('s', 't', 'o', 'p')
                await pilot.press('enter')
                await pilot.pause()
                assert app._turn_active is True, 'it started a second turn instead of steering'
                release.set()

                assert await _wait_for(pilot, lambda: not app._turn_active)
                seen = [
                    part.content
                    for message in app._session.messages
                    for part in getattr(message, 'parts', [])
                    if type(part).__name__ == 'UserPromptPart'
                ]
                # The message is in the conversation the model saw, in order.
                assert seen == ['first', 'stop']

    asyncio.run(inner())


def test_the_queued_message_lands_after_the_tool_results():
    """The seam matters: the agent finishes its tool batch, then reads the note.

    Injecting mid-tool would answer a call that has no result yet, which
    pydantic-ai refuses; injecting at the end would make it a separate turn.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()

            agent = Agent(TestModel(call_tools=['slow'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def slow() -> str:
                await release.wait()
                return 'result'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                for _ in range(40):
                    await pilot.pause()
                    if app._current_run is not None:
                        break

                await pilot.press('n', 'o', 'w')
                await pilot.press('enter')
                await pilot.pause()
                release.set()
                assert await _wait_for(pilot, lambda: not app._turn_active)

                flat = [part for message in app._session.messages for part in message.parts]
                kinds = [type(part).__name__ for part in flat]
                contents = [getattr(part, 'content', None) for part in flat]
                # The order the model read: the tool answered first, the note
                # came right after its result, and only then the next response.
                assert kinds.index('ToolReturnPart') < contents.index('now')
                assert contents.index('now') == kinds.index('ToolReturnPart') + 1
                # One request with the note, not a second turn's prompt.
                assert contents.count('now') == 1
                assert contents.index('now') < kinds.index('TextPart')

    asyncio.run(inner())


def test_a_message_that_arrives_as_the_agent_finishes_gives_it_one_more_step():
    """The other seam: queued at the end, read instead of closing the run.

    Without this, a correction typed while the model was writing its last answer
    would sit in the queue until the user sent something else.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            from pydantic_ai.messages import ModelMessage
            from pydantic_ai.models.function import AgentInfo, FunctionModel

            answered = asyncio.Event()
            release = asyncio.Event()

            async def stream_function(messages: list[ModelMessage], _info: AgentInfo):
                # The first answer waits before finishing, so the message is
                # queued while the run is still inside its last step.
                if not answered.is_set():
                    answered.set()
                    await release.wait()
                yield 'done'

            agent = Agent(FunctionModel(stream_function=stream_function), instructions='x')
            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                assert await _wait_for(pilot, answered.is_set)

                field = app.query_one(PromptInput)
                field.value = 'one more thing'
                field.post_message(Input.Submitted(field, 'one more thing'))
                await pilot.pause()
                assert app._turn_active is True
                release.set()

                assert await _wait_for(pilot, lambda: not app._turn_active)
                prompts = [
                    part.content
                    for message in app._session.messages
                    for part in getattr(message, 'parts', [])
                    if type(part).__name__ == 'UserPromptPart'
                ]
                # It was read: the run got one more step instead of ending with
                # the message queued and nobody looking at it.
                assert prompts == ['go', 'one more thing']
                assert app.query_one(PromptInput).value == ''

    asyncio.run(inner())


def test_a_message_that_misses_the_last_seam_comes_back_to_the_prompt():
    """No silent loss: an unread message returns to the prompt, with a notice."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='done'), instructions='x')
            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()

                # What `_take_undelivered` sees after a turn whose queue closed
                # before the message could be delivered.
                from src.ui.turn_mixin import Steer

                widget = await app.query_one(ChatLog).add_user('late', queued=True)
                app._steering.append(Steer(text='late', widget=widget))

                leftovers = app._take_undelivered()
                assert [entry.text for entry in leftovers] == ['late']
                assert app._steering == []

    asyncio.run(inner())


def test_the_queued_line_loses_its_mark_once_the_run_reads_it():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()

            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                for _ in range(40):
                    await pilot.pause()
                    if app._current_run is not None:
                        break

                await pilot.press('h', 'i')
                await pilot.press('enter')
                await pilot.pause()

                queued_lines = [text for text in _chat_texts(app) if 'queued' in text]
                assert queued_lines, 'the line does not say that it is waiting'
                assert release is not None

                release.set()
                assert await _wait_for(pilot, lambda: not app._turn_active)
                assert not [text for text in _chat_texts(app) if 'queued' in text]

    asyncio.run(inner())


def test_commands_that_would_swap_the_session_are_refused_mid_turn():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()

            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                for _ in range(40):
                    await pilot.pause()
                    if app._current_run is not None:
                        break
                session_before = app._session.id

                field = app.query_one(PromptInput)
                field.value = '/new'
                field.post_message(Input.Submitted(field, '/new'))
                await pilot.pause()

                assert app._session.id == session_before
                assert any('/new is not available' in text for text in _chat_texts(app))
                # The command is handed back, not swallowed.
                assert app.query_one(PromptInput).value == '/new'

                release.set()
                assert await _wait_for(pilot, lambda: not app._turn_active)

    asyncio.run(inner())


def test_the_menu_enter_is_refused_too_not_only_the_typed_path():
    """`Enter` on `/new` with the menu open is a second door into the commands.

    The guard used to live in `on_input_submitted` only, and the menu calls
    `handle_command` directly: typing `/new` and pressing `Enter` mid-turn
    swapped the session and wiped the chat. The check now sits at the choke
    point both paths go through.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()

            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                assert await _wait_for(pilot, lambda: app._current_run is not None)
                session_before = app._session.id
                children_before = len(app.query_one(ChatLog).children)

                # What the UI does: type the command, the menu opens, Enter picks it.
                field = app.query_one(PromptInput)
                field.value = '/new'
                await pilot.pause()
                menu = app.query_one(CommandMenu)
                assert menu.display, 'the menu should be open, or the test is not testing the menu'
                menu.highlighted = 0
                await app.menu_choose()
                await pilot.pause()

                assert app._session.id == session_before, 'the session was swapped mid-turn'
                # The chat keeps the turn in progress and gains only the notice.
                assert len(app.query_one(ChatLog).children) == children_before + 1
                assert any('/new is not available' in text for text in _chat_texts(app))
                assert app.query_one(PromptInput).value == '/new'

                release.set()
                assert await _wait_for(pilot, lambda: not app._turn_active)

    asyncio.run(inner())


def test_load_is_refused_mid_turn_and_never_opens_the_picker():
    """A refused `/load` does not open the session picker, and does not crash.

    The picker is a modal: opening one while a turn trusts the worker that
    opened it is how `push_screen_wait` ends up waiting on a future nobody
    resolves. A refusal has to stop before the screen, not after it.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()

            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                # A saved session to load, so the picker would have something to show.
                saved = app._store.create_session()
                app._store.save(saved)
                app.start_turn('go')
                assert await _wait_for(pilot, lambda: app._current_run is not None)
                session_before = app._session.id

                field = app.query_one(PromptInput)
                field.value = '/load'
                await pilot.pause()
                app.query_one(CommandMenu).highlighted = 0
                await app.menu_choose()
                await pilot.pause()

                assert type(app.screen).__name__ != 'SessionPicker'
                assert app._session.id == session_before
                assert any('/load is not available' in text for text in _chat_texts(app))

                release.set()
                assert await _wait_for(pilot, lambda: not app._turn_active)
                # And the app is still the app: the turn ended normally.
                assert app.query_one(PromptInput).value == '/load'

    asyncio.run(inner())


def test_a_turn_never_writes_its_history_into_a_session_opened_under_it():
    """The safety net behind the guard: the run saves to the session it started on.

    Even if a command slipped through, the conversation that was just loaded
    must not be overwritten by the turn of the one it replaced.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()

            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                assert await _wait_for(pilot, lambda: app._current_run is not None)
                old_session = app._session

                # Simulate the swap the guard is there to prevent.
                new_session = app._store.create_session()
                app.bind_session(new_session)

                release.set()
                assert await _wait_for(pilot, lambda: not app._turn_active)

                # The turn's work is in the session it belonged to...
                assert old_session.messages
                # ...and the one that replaced it is untouched.
                assert new_session.messages == []
                reloaded = app._store.load(new_session.id)
                assert reloaded is not None and reloaded.messages == []

    asyncio.run(inner())


def test_quit_does_not_raise_from_the_command_dispatch():
    """`/quit` is a plain function and used to be `await`ed anyway.

    That `TypeError` came out as a worker failure: the app closed, and the log
    showed a crash on the way out.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp, Agent(TestModel(custom_output_text='ok'), instructions='x'))
            async with app.run_test() as pilot:
                await pilot.pause()
                await app.handle_command('/quit')
                await pilot.pause()

    asyncio.run(inner())


# --- abort -----------------------------------------------------------------


def test_the_first_esc_asks_and_the_second_one_stops_the_turn():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()

            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                for _ in range(40):
                    await pilot.pause()
                    if app._current_run is not None:
                        break
                assert app._turn_active is True

                await pilot.press('escape')
                await pilot.pause()
                # Nothing stopped yet: it is a question, not an order.
                assert app._turn_active is True
                assert 'again' in _status(app)
                assert app._abort_deadline is not None

                await pilot.press('escape')
                assert await _wait_for(pilot, lambda: not app._turn_active)
                assert any('Turn cancelled.' in text for text in _chat_texts(app))
                # No error: stopping on purpose is not a failure.
                assert not any('Error' in text for text in _chat_texts(app))
                assert _status(app) == ''

    asyncio.run(inner())


def test_any_other_key_cancels_the_abort_request():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()

            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                for _ in range(40):
                    await pilot.pause()
                    if app._current_run is not None:
                        break

                await pilot.press('escape')
                await pilot.pause()
                assert app._abort_deadline is not None

                await pilot.press('x')
                await pilot.pause()
                assert app._abort_deadline is None
                assert _status(app) == ''
                # The turn is still going: the confirmation did not survive.
                assert app._turn_active is True

                # A third `Esc` asks again instead of stopping (the window is new).
                await pilot.press('escape')
                await pilot.pause()
                assert app._turn_active is True
                assert app._abort_deadline is not None

                release.set()
                assert await _wait_for(pilot, lambda: not app._turn_active)

    asyncio.run(inner())


def test_the_abort_request_expires_on_its_own(monkeypatch):
    """Three seconds pass: the question goes away, the agent keeps working."""
    import src.ui.turn_mixin as turnmod

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()

            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                for _ in range(40):
                    await pilot.pause()
                    if app._current_run is not None:
                        break

                await pilot.press('escape')
                await pilot.pause()
                assert app._abort_deadline is not None

                # The window is a constant of the app, so a shorter one is enough.
                monkeypatch.setattr(turnmod, 'ABORT_WINDOW', 0.05)
                app._stop_abort_timer()
                app._abort_timer = app.set_timer(0.05, app._expire_abort)

                assert await _wait_for(pilot, lambda: app._abort_deadline is None)
                assert app._turn_active is True
                assert _status(app) == ''

                release.set()
                assert await _wait_for(pilot, lambda: not app._turn_active)

    asyncio.run(inner())


def test_the_second_esc_still_stops_the_turn_when_the_window_is_open():
    """The guard is a deadline, not a flag: it survives the round trip to the bar."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()

            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                for _ in range(40):
                    await pilot.pause()
                    if app._current_run is not None:
                        break

                # The first press only asks: the deadline is in the future.
                assert app.request_abort() is False
                assert app._abort_deadline is not None
                assert app._abort_deadline > time.monotonic()
                # The second one, before it expires, is the confirmation.
                assert app.request_abort() is True
                app.abort_turn()
                assert await _wait_for(pilot, lambda: not app._turn_active)
                release.set()

    asyncio.run(inner())


def test_two_messages_sent_back_to_back_do_not_start_two_turns():
    """The second one arrives before the worker starts: it steers, it does not race.

    `_turn_active` is set when the message is accepted, not when the worker gets
    the loop, so there is no window where the same keystroke starts a second
    turn on top of the first.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()

            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                field = app.query_one(PromptInput)
                # Both submits in the same tick: the worker has not started yet.
                for text in ('first', 'second'):
                    field.value = text
                    field.post_message(Input.Submitted(field, text))
                await pilot.pause()

                workers = [worker for worker in app.workers if worker.name]
                assert len([worker for worker in workers if worker.name == 'run_turn']) <= 1

                assert await _wait_for(pilot, lambda: app._current_run is not None)
                release.set()
                assert await _wait_for(pilot, lambda: not app._turn_active)

                prompts = [
                    part.content
                    for message in app._session.messages
                    for part in getattr(message, 'parts', [])
                    if type(part).__name__ == 'UserPromptPart'
                ]
                assert prompts == ['first', 'second']

    asyncio.run(inner())


def test_an_aborted_turn_is_saved_and_the_next_message_still_works():
    """Stopping is not breaking: the history on disk is valid and the chat goes on."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()

            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='answered'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                for _ in range(40):
                    await pilot.pause()
                    if app._current_run is not None:
                        break

                await pilot.press('escape')
                await pilot.press('escape')
                assert await _wait_for(pilot, lambda: not app._turn_active)

                # What the run completed is on disk, not only in memory.
                saved = app._store.load(app._session.id)
                assert saved is not None
                assert list(saved.messages) == list(app._session.messages)
                # The call that never answered is closed out, so the history is
                # one a provider will accept as the prefix of the next turn.
                assert app._session.messages

                # And the conversation continues. The tool is free now, so the
                # new turn ends with the model's answer.
                release.set()
                await pilot.press('a', 'g', 'a', 'i', 'n')
                await pilot.press('enter')
                assert await _wait_for(pilot, lambda: not app._turn_active)
                texts = _chat_texts(app)
                assert not any('Error' in text for text in texts)
                assert any('answered' in text for text in texts)

    asyncio.run(inner())


# --- shell commands --------------------------------------------------------

PID_FILE = 'sigil-abort-test.pid'


def test_aborting_kills_the_shell_commands_the_turn_spawned():
    """A command in flight does not outlive the abort: its process group dies.

    Cancelling the asyncio task is not enough: the command runs in a worker
    thread and in its own process group, so it would keep going until its own
    timeout and the agent would look stopped while a build kept churning.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / PID_FILE
            agent = Agent(TestModel(call_tools=['run'], custom_output_text='done'), instructions='x')
            started = asyncio.Event()

            @agent.tool_plain
            async def run() -> str:
                started.set()
                # The command reports its own pid and then blocks, through the
                # same path the agent's `run` tool uses.
                result = await asyncio.to_thread(
                    execute_command,
                    f'echo $$ > {pid_file}; sleep 60',
                    60,
                )
                return str(result.returncode)

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                assert await _wait_for(pilot, started.is_set, attempts=100)
                assert await _wait_for(pilot, lambda: pid_file.exists() and pid_file.read_text().strip())
                pid = int(pid_file.read_text().strip())
                assert _alive(pid), 'the command never started'

                app.request_abort()
                app.abort_turn()
                assert await _wait_for(pilot, lambda: not app._turn_active)
                # The process went with the turn, not with its timeout.
                assert await _wait_for(pilot, lambda: not _alive(pid), attempts=40)

    asyncio.run(inner())


def test_quitting_mid_turn_stops_the_run_and_kills_its_commands():
    """`Ctrl+Q` is the exit: it does not leave a run and a build behind."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()
            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                for _ in range(40):
                    await pilot.pause()
                    if app._current_run is not None:
                        break

                app.action_quit()
                await pilot.pause()
                # `exit()` was asked for: the app is on its way out.
                assert app._exit is True
                # Nothing is left in the registry: the kill happened on the way out.
                assert running_commands.kill_all() == 0

    asyncio.run(inner())


def _alive(pid: int) -> bool:
    """Whether the process is still there. A zombie counts as gone."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    status = Path(f'/proc/{pid}/stat')
    if status.exists():
        # `state` is the third field, after the pid and the name in parentheses.
        try:
            return status.read_text().split(')')[-1].split()[0] != 'Z'
        except OSError, IndexError:
            return True
    return True


def test_the_model_shortcut_is_refused_mid_turn_as_well():
    """`ctrl+m` reaches the picker without going through `handle_command`.

    The command is refused at the dispatch, but the shortcut is its own path:
    without the same check it fetched the catalogue and opened the picker on top
    of a running turn.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            release = asyncio.Event()

            agent = Agent(TestModel(call_tools=['hold'], custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def hold() -> str:
                await release.wait()
                return 'ok'

            app = _make_app(tmp, agent)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.start_turn('go')
                assert await _wait_for(pilot, lambda: app._current_run is not None)

                await app.open_models()
                await pilot.pause()

                assert type(app.screen).__name__ == 'Screen'
                assert any('/model is not available' in text for text in _chat_texts(app))

                release.set()
                assert await _wait_for(pilot, lambda: not app._turn_active)

    asyncio.run(inner())
