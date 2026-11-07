"""What a tool says about itself, and how it reaches the transcript.

The reason this exists: the chat used to print `str(result)`, so a successful `run`
showed up as `returncode=0 stdout='...' stderr=None timed_out=False`. The one line a
reader needs — how it went — was in there, buried in a Pydantic repr.

Two layers are tested apart. The engine decides the **text and the status** and never
paints; the UI decides **where it goes and in what color**.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from rich.text import Text

from src.engine.files.models.file_content import FileContent
from src.engine.files.models.patch_result import PatchResult
from src.engine.files.models.write_result import WriteResult
from src.engine.files.tools import describe_patch, describe_read, describe_write
from src.engine.preferences import PreferencesStore
from src.engine.sessions.session_store import SessionStore
from src.engine.shell.models.run_tool_output import RunToolOutput
from src.engine.shell.tools import describe_run
from src.engine.tooling.events import ToolCallEvent
from src.engine.tooling.logging import ToolLoggerHub
from src.engine.tooling.permission import BUILTIN_TOOLS
from src.engine.tooling.presentation import (
    DISPLAY_NAMES,
    ToolSummary,
    display_name,
    human_bytes,
    summary_of,
)
from src.engine.tooling.registrar import ToolRegistrar
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog, ToolCallBlock, ToolGroupHeader
from src.ui.widgets.rows import fit

OK = RunToolOutput(returncode=0, stdout='.........\n9 passed in 0.45s\n')
FAILED = RunToolOutput(
    returncode=1,
    stdout='FAILED tests/test_x.py::test_y - NameError\n1 failed, 8 passed in 0.61s\n',
)


# --- the presenters ---------------------------------------------------------


def test_a_passing_test_run_keeps_the_line_a_reader_wants():
    summary = describe_run({}, [OK])
    assert summary == ToolSummary('9 passed in 0.45s', 'good')


def test_a_failing_test_run_is_bad_but_the_call_returned():
    summary = describe_run({}, [FAILED])
    assert summary is not None
    assert summary.status == 'bad'
    assert summary.text == '1 failed, 8 passed in 0.61s'


def test_a_command_with_no_test_summary_falls_back_to_the_exit_code():
    """A linter or a build prints no summary; the exit code is always true."""
    summary = describe_run({}, [RunToolOutput(returncode=2, stderr='boom\n')])
    assert summary == ToolSummary('exit 2', 'bad')


def test_a_timeout_says_so_instead_of_a_return_code():
    summary = describe_run({}, [RunToolOutput(returncode=0, timed_out=True)])
    assert summary == ToolSummary('timed out', 'bad')


def test_several_commands_get_a_count_not_one_command_detail():
    """`run` takes a list: one row for the call, so one summary for the batch."""
    summary = describe_run({}, [OK, FAILED])
    assert summary == ToolSummary('2 cmds · 1 failed', 'bad')
    assert describe_run({}, [OK, OK]) == ToolSummary('2 cmds · ok', 'good')


def test_a_write_reports_what_it_did_with_the_file():
    created = describe_write({}, WriteResult(path='/w/a.py', bytes_written=2048, created=True, appended=False))
    assert created == ToolSummary('created · 2.0 KB')
    assert describe_write(
        {}, WriteResult(path='/w/a.py', bytes_written=120, created=False, appended=True)
    ) == ToolSummary('appended · 120 B')


def test_a_patch_counts_replacements_and_not_lines():
    """`PatchResult` has bytes, not line counts; `git diff` answers the repo question."""
    result = PatchResult(
        path='/w/a.py',
        mode='string',
        replacements=3,
        bytes_before=100,
        bytes_after=140,
        preview='',
    )
    assert describe_patch({}, result) == ToolSummary('3 replacements')


def test_a_read_reports_the_slice_it_returned():
    whole = FileContent(
        path='/w/a.py', size=400, truncated=False, binary=False, encoding='utf-8', content='a\nb\nc\n', total_lines=3
    )
    assert describe_read({}, whole) == ToolSummary('3 lines')
    sliced = FileContent(
        path='/w/a.py',
        size=400,
        truncated=False,
        binary=False,
        encoding='utf-8',
        content='b\nc\n',
        start_line=2,
        end_line=3,
        total_lines=30,
    )
    assert describe_read({}, sliced) == ToolSummary('2 of 30 lines')


def test_a_bad_result_shape_says_nothing_instead_of_raising():
    """A presenter that trips must not fail the call the model is waiting for."""
    assert describe_run({}, 'not a list') is None
    assert describe_write({}, 'not a write') is None
    assert summary_of(lambda args, result: 1 / 0, {}, None) is None  # type: ignore[arg-type, return-value]


def test_a_summary_is_one_short_line():
    """The clipping is `summary_of`'s job, not the presenter's: one place, always on."""
    long = summary_of(describe_run, {}, [RunToolOutput(returncode=0, stdout='x' * 400 + ' 5 passed\n')])
    assert long is not None
    assert '\n' not in long.text
    assert long.text.endswith('…')
    assert len(long.text) <= 120


def test_every_builtin_tool_has_a_display_name():
    """The table is a table, so something has to keep it honest."""
    missing = set(BUILTIN_TOOLS) - set(DISPLAY_NAMES)
    assert not missing, f'no display name for: {sorted(missing)}'
    assert display_name('write_file') == 'write'
    # An MCP tool has no entry, and showing its real name is what makes it stand out.
    assert display_name('mcp_github_create_issue') == 'mcp_github_create_issue'


def test_human_bytes_grows_the_unit_and_keeps_the_number_short():
    assert human_bytes(840) == '840 B'
    assert human_bytes(2048) == '2.0 KB'
    assert human_bytes(1024 * 1024 * 3) == '3.0 MB'


# --- the wiring ------------------------------------------------------------


def test_the_summary_travels_in_the_tool_event():
    """The presenter is declared with the tool and lands in the event on success."""
    logger = ToolLoggerHub()
    events: list[ToolCallEvent] = []
    logger.add_sink(events.append)
    agent = Agent(TestModel(custom_output_text='ok'), instructions='x')

    @ToolRegistrar(agent, logger).tool(describe=lambda args, result: ToolSummary(f'wrote {result}', 'good'))
    def remember(text: str) -> str:
        return text

    assert remember('hola') == 'hola'
    done = [event for event in events if event.phase == 'success']
    assert done
    assert done[-1].summary == ToolSummary('wrote hola', 'good')
    # The raw preview is still there, and is still a different thing: the summary is
    # what the tool chose to say, the preview is the result cut to one line.
    assert done[-1].result_preview == 'hola'
    assert done[-1].duration_ms is not None


# --- the row ---------------------------------------------------------------


def test_fit_puts_the_right_half_flush_and_clips_the_left():
    left = Text('  │ write tests/test_roadmap.py')
    right = Text('42 lines · 1.2 KB')
    row = fit(left, right, 60)
    assert row.cell_len == 60
    assert row.plain.endswith('42 lines · 1.2 KB')


def test_fit_drops_the_right_half_rather_than_leaving_a_stub():
    row = fit(Text('  │ write tests/test_roadmap.py'), Text('9 passed in 0.45s'), 20)
    assert '9 passed' not in row.plain
    assert row.cell_len <= 20


def test_the_block_shows_the_summary_and_the_duration_instead_of_a_repr():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                await chat.add_tool_call('run', {'cmds': ['pytest -q']})
                await pilot.pause()
                block = app.query_one(ToolCallBlock)
                # Pending is animated, not a frozen check or an ellipsis.
                assert block.pending
                first = block._state_mark()
                block._advance_pending()
                assert block._state_mark() != first
                assert block._pending_timer is not None
                assert 'running' in block._header().plain
                assert '9 passed' not in block._header().plain

                chat.set_tool_summary('run', ToolSummary('9 passed in 0.45s', 'good'), 450)
                await chat.add_tool_result('run', OK)
                await pilot.pause()
                header = block._header().plain
                assert '9 passed in 0.45s' in header
                assert '450ms' in header
                assert 'returncode=' not in header
                assert block._state_mark() == '✓'
                assert block._pending_timer is None
                assert 'running' not in header
                # Flush right on the width the block actually has.
                assert block._header().cell_len == block.size.width

    asyncio.run(inner())


def test_a_failed_result_is_red_and_marks_the_outcome_as_failed():
    """A bad result is a visible failure even when the invocation returned."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                await chat.add_tool_call('run', {'cmds': ['pytest -q']})
                chat.set_tool_summary('run', ToolSummary('1 failed', 'bad'), 610)
                await chat.add_tool_result('run', FAILED)
                await pilot.pause()
                block = app.query_one(ToolCallBlock)
                assert block._state_mark() == '✗'
                summary = block._outcome()
                assert summary is not None
                assert '1 failed' in summary.plain
                # Painted with the theme's error color, which is what "bad" means.
                assert app.theme_variables['error'] in [str(span.style) for span in summary.spans]

    asyncio.run(inner())


def test_the_header_follows_the_width_and_drops_the_outcome_when_there_is_no_room():
    """The right edge stays flush, and the right half goes before the left becomes a stub.

    The side panel takes 34 columns, so the block in a 70-column terminal is about 34
    cells: enough for the call, not for the call and its result. The rule is that the
    result is dropped, never squeezed — half a clipped sentence reads like a whole one.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                await chat.add_tool_call('write_file', {'path': 'tests/test_roadmap.py', 'content': 'x' * 400})
                chat.set_tool_summary('write_file', ToolSummary('created · 1.2 KB', 'neutral'), 12)
                await chat.add_tool_result('write_file', 'ok')
                await pilot.pause()
                block = app.query_one(ToolCallBlock)
                assert block._header().cell_len == block.size.width
                assert 'created · 1.2 KB' in block._header().plain

                # Narrower, but still wide enough for both halves.
                await pilot.resize_terminal(100, 30)
                await pilot.pause()
                assert block._header().cell_len == block.size.width
                assert 'created · 1.2 KB' in block._header().plain

                # Too narrow: the outcome goes, the call stays readable.
                await pilot.resize_terminal(60, 30)
                await pilot.pause()
                header = block._header().plain
                assert 'created' not in header
                assert 'write' in header
                assert block._header().cell_len <= block.size.width

    asyncio.run(inner())


def test_a_reloaded_session_has_no_duration_to_lie_about():
    """`duration_ms` lives in the event and is not persisted: draw nothing, not 0.0s."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                block = ToolCallBlock('run', {'cmds': ['ls']}, classes='tool-call')
                await app.query_one(ChatLog).mount(block)
                block.set_result(RunToolOutput(returncode=0, stdout='a\n'))
                await pilot.pause()
                header = block._header().plain
                assert 'ms' not in header
                assert '0.0s' not in header

    asyncio.run(inner())


# --- the batch ---------------------------------------------------------------


def _rows(chat: ChatLog) -> list[ToolCallBlock]:
    return [child for child in chat.children if isinstance(child, ToolCallBlock)]


def _headers(chat: ChatLog) -> list[ToolGroupHeader]:
    return [child for child in chat.children if isinstance(child, ToolGroupHeader)]


async def _batch(chat: ChatLog, calls: list[tuple[str, dict]]) -> None:
    for name, args in calls:
        await chat.add_tool_call(name, args)


def test_a_batch_gets_one_header_and_a_single_call_does_not():
    """A batch is a run of calls with no answer in between, and it stays open.

    The header is created with the first call — there is no way to know a second one is
    coming — and it stays hidden until there is a second row. Nothing closes the batch
    except text, a new response, a notice or a user message: a call that has already
    returned does not, because the model's step is not over.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)

                await _batch(chat, [('run', {'cmds': ['ls']})])
                await pilot.pause()
                headers = _headers(chat)
                assert len(headers) == 1
                # One call needs no grouping: the row explains itself.
                assert headers[0].display is False

                # A second call of the same step joins the same batch.
                await _batch(chat, [('read_file', {'path': 'a.py'})])
                await pilot.pause()
                assert len(_headers(chat)) == 1
                assert headers[0].display is True
                assert headers[0]._rows == _rows(chat)

                # The model answers: the next calls belong to a new step.
                await chat.begin_response()
                await chat.add_text('now the other one')
                await _batch(chat, [('run', {'cmds': ['pwd']}), ('run', {'cmds': ['whoami']})])
                await pilot.pause()
                assert len(_headers(chat)) == 2
                assert _headers(chat)[1].display is True

    asyncio.run(inner())


def test_the_header_counts_what_is_left_and_then_stops_counting():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                await _batch(chat, [('run', {'cmds': ['a']}), ('run', {'cmds': ['b']}), ('read_file', {'path': 'c'})])
                await pilot.pause()
                header = _headers(chat)[0]
                assert '3 tools' in header.render().plain
                assert '0 / 3 done' in header.render().plain

                await chat.add_tool_result('run', 'ok')
                await pilot.pause()
                assert '1 / 3 done' in header.render().plain

                await chat.add_tool_result('run', 'ok')
                await chat.add_tool_result('read_file', 'ok')
                await pilot.pause()
                # Closed: no counter, because there is nothing left to wait for.
                assert '[' not in header.render().plain
                assert header.closed is True

    asyncio.run(inner())


def test_the_header_says_how_many_calls_raised():
    """A raised call is counted; a bad result is not, and shows on its own row."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                await _batch(chat, [('run', {'cmds': ['a']}), ('read_file', {'path': 'b'})])
                await chat.add_tool_error('run', 'RuntimeError: boom')
                await chat.add_tool_result('read_file', 'ok')
                await pilot.pause()
                header = _headers(chat)[0]
                assert '1 error' in header.render().plain
                # It survives a reload: the rows keep their own state.
                assert _rows(chat)[0]._state_mark() == '✗'

    asyncio.run(inner())


def test_text_between_calls_starts_a_new_batch():
    """The grouping is inference, and this is the rule: a run of calls with no answer."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                await _batch(chat, [('run', {'cmds': ['a']}), ('run', {'cmds': ['b']})])
                await chat.add_tool_result('run', 'ok')
                await chat.add_tool_result('run', 'ok')
                await pilot.pause()

                # The model answers, then asks for more: a second step, not the same one.
                await chat.begin_response()
                await chat.add_text('now the tests')
                await _batch(chat, [('read_file', {'path': 'x'}), ('read_file', {'path': 'y'})])
                await pilot.pause()
                assert len(_headers(chat)) == 2

    asyncio.run(inner())


# --- collapsing --------------------------------------------------------------


def test_output_is_collapsed_by_default_and_says_nothing_extra():
    """A row that says `9 passed` does not need a second line saying there is output."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                await chat.add_tool_call('run', {'cmds': ['pytest -q']})
                chat.set_tool_summary('run', ToolSummary('9 passed in 0.45s', 'good'), 450)
                await chat.add_tool_result('run', RunToolOutput(returncode=0, stdout='one\ntwo\nthree\n'))
                await pilot.pause()
                block = _rows(chat)[0]
                assert block._expanded is False
                assert block._body() == []

    asyncio.run(inner())


def test_a_failure_shows_its_lines_even_collapsed():
    """Hiding a failure behind a preference nobody set would hide the actionable part."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                await chat.add_tool_call('run', {'cmds': ['pytest -q']})
                chat.set_tool_summary('run', ToolSummary('1 failed', 'bad'), 610)
                output = 'FAILED tests/test_x.py::test_y - NameError\nline two\nline three\nline four\nline five\n'
                await chat.add_tool_result('run', RunToolOutput(returncode=1, stdout=output))
                await pilot.pause()
                block = _rows(chat)[0]
                body = [line.plain for line in block._body()]
                assert any('NameError' in line for line in body)
                assert all('↳' in line for line in body if 'FAILED' in line)
                # Capped: the transcript is not a terminal.
                assert len(body) <= 4

    asyncio.run(inner())


def test_a_tool_that_raised_shows_the_error_even_collapsed():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                await chat.add_tool_call('read_file', {'path': 'nope.py'})
                await chat.add_tool_error('read_file', 'FileToolError: no such file')
                await pilot.pause()
                body = [line.plain for line in _rows(chat)[0]._body()]
                assert any('no such file' in line for line in body)

    asyncio.run(inner())


def test_a_tool_without_a_presenter_says_that_there_is_output():
    """Otherwise the row is a ✓ with nothing behind it — an MCP tool, for instance."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                await chat.add_tool_call('mcp_github_create_issue', {'title': 'x'})
                await chat.add_tool_result('mcp_github_create_issue', 'issue #12 created\nsecond line')
                await pilot.pause()
                body = [line.plain for line in _rows(chat)[0]._body()]
                assert body and '2 lines' in body[0]

    asyncio.run(inner())


def test_tools_expanded_repaints_what_is_already_on_screen_and_is_remembered():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            prefs = PreferencesStore(path=Path(tmp) / 'ui.json')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
                preferences_store=prefs,
            )
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                await chat.add_tool_call('run', {'cmds': ['ls']})
                chat.set_tool_summary('run', ToolSummary('2 lines', 'neutral'), 5)
                await chat.add_tool_result('run', 'alpha\nbeta')
                await pilot.pause()
                block = _rows(chat)[0]
                assert block._body() == []

                await app.handle_tools('expanded', chat)
                await pilot.pause()
                assert block._expanded is True
                assert [line.plain.split('│')[-1].strip() for line in block._body()] == ['alpha', 'beta']
                assert prefs.load().tools_expanded is True

                await app.handle_tools('collapsed', chat)
                await pilot.pause()
                assert block._expanded is False
                assert prefs.load().tools_expanded is False

    asyncio.run(inner())


def test_should_stop_pending_animation_on_interrupted_turn_and_unmount() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                await chat.add_tool_call('run', {'cmds': ['sleep 10']})
                block = app.query_one(ToolCallBlock)
                assert block._pending_timer is not None
                chat.finalize()
                assert not block.pending
                assert block._state_mark() == '✗'
                assert block._pending_timer is None
                assert block.raised

                await chat.add_tool_call('read_file', {'path': 'a.py'})
                other = list(app.query(ToolCallBlock))[-1]
                assert other._pending_timer is not None
                await other.remove()
                assert other._pending_timer is None

    asyncio.run(scenario())
