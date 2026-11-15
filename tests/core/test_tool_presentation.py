"""What a tool says about itself, and how it reaches the transcript.

The reason this exists: the chat used to print `str(result)`, so a successful `run`
showed up as `returncode=0 stdout='...' stderr=None timed_out=False`. The one line a
reader needs — how it went — was in there, buried in a Pydantic repr.

Two layers are tested apart. The engine decides the **text and the status** and never
paints; the UI decides **where it goes and in what color**.
"""

from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.files.models.file_content import FileContent
from src.engine.files.models.patch_result import PatchResult
from src.engine.files.models.write_result import WriteResult
from src.engine.files.tools import describe_patch, describe_read, describe_write
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
