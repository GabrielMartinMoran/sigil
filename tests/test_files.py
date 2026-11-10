"""Tests for the file tools."""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from src.engine.files import paths
from src.engine.files.models.file_tool_error import FileToolError
from src.engine.files.paths import resolve as resolve_path
from src.engine.files.tools import register_file_tools


class DummyAgent:
    def __init__(self) -> None:
        self.tools = {}

    def _register(self, func, kwargs):
        name = kwargs.get('name') or func.__name__
        self.tools[name] = func
        return func

    def tool_plain(self, func, **kwargs):
        return self._register(func, kwargs)

    def tool(self, func, **kwargs):
        return self._register(func, kwargs)


def _tools():
    agent = DummyAgent()
    register_file_tools(agent)
    return agent.tools


def test_write_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tools = _tools()
    result = tools['write_file'](path='notes.txt', content='hello\n')
    assert result.created is True
    assert result.bytes_written == 6

    read = tools['read_file'](path='notes.txt')
    assert read.content == 'hello\n'
    assert read.binary is False
    assert read.encoding == 'utf-8'
    assert read.size == 6


def test_read_line_range(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('a.txt').write_text('one\ntwo\nthree\n')
    tools = _tools()
    read = tools['read_file'](path='a.txt', start_line=2, end_line=2)
    assert read.content == 'two\n'
    assert read.start_line == 2
    assert read.end_line == 2
    assert read.total_lines == 3


def test_read_binary_returns_base64(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('bin.dat').write_bytes(b'\x00\x01\x02')
    tools = _tools()
    read = tools['read_file'](path='bin.dat')
    assert read.binary is True
    assert read.encoding == 'base64'


def test_patch_string_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('a.txt').write_text('one\ntwo\nthree\n')
    tools = _tools()
    result = tools['patch_file'](path='a.txt', old_string='two', new_string='TWO')
    assert result.mode == 'string'
    assert result.replacements == 1
    assert Path('a.txt').read_text() == 'one\nTWO\nthree\n'


def test_patch_requires_unique_match(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('a.txt').write_text('x\nx\n')
    tools = _tools()
    with pytest.raises(FileToolError, match='appears 2 times'):
        tools['patch_file'](path='a.txt', old_string='x', new_string='y')


def test_patch_replace_all(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('a.txt').write_text('x\nx\n')
    tools = _tools()
    result = tools['patch_file'](path='a.txt', old_string='x', new_string='y', replace_all=True)
    assert result.replacements == 2
    assert Path('a.txt').read_text() == 'y\ny\n'


def test_patch_line_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('a.txt').write_text('one\ntwo\nthree\n')
    tools = _tools()
    result = tools['patch_file'](path='a.txt', start_line=2, end_line=2, new_string='TWO')
    assert result.mode == 'lines'
    assert Path('a.txt').read_text() == 'one\nTWO\nthree\n'


def test_patch_line_mode_validates_expected_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('a.txt').write_text('one\ntwo\nthree\n')
    tools = _tools()
    with pytest.raises(FileToolError, match='expected_text'):
        tools['patch_file'](path='a.txt', start_line=2, end_line=2, new_string='X', expected_text='wrong')


def test_patch_line_mode_accepts_expected_text_without_trailing_newline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('a.txt').write_text('one\ntwo\nthree\n')
    tools = _tools()
    tools['patch_file'](path='a.txt', start_line=2, end_line=2, new_string='TWO', expected_text='two')
    assert Path('a.txt').read_text() == 'one\nTWO\nthree\n'


def test_patch_rejects_expected_text_in_string_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('a.txt').write_text('one\ntwo\n')
    tools = _tools()
    with pytest.raises(FileToolError, match='expected_text'):
        tools['patch_file'](path='a.txt', old_string='two', new_string='TWO', expected_text='two')


def test_patch_preserves_crlf(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('a.txt').write_bytes(b'one\r\ntwo\r\nthree\r\n')
    tools = _tools()
    tools['patch_file'](path='a.txt', start_line=2, end_line=2, new_string='TWO')
    assert Path('a.txt').read_bytes() == b'one\r\nTWO\r\nthree\r\n'

    tools['patch_file'](path='a.txt', old_string='three', new_string='THREE')
    assert Path('a.txt').read_bytes() == b'one\r\nTWO\r\nTHREE\r\n'


def test_patch_line_mode_keeps_missing_trailing_newline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('a.txt').write_text('one\ntwo')
    tools = _tools()
    tools['patch_file'](path='a.txt', start_line=2, end_line=2, new_string='TWO')
    assert Path('a.txt').read_text() == 'one\nTWO'


def test_read_line_range_preserves_crlf(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('a.txt').write_bytes(b'one\r\ntwo\r\nthree\r\n')
    tools = _tools()
    read = tools['read_file'](path='a.txt', start_line=2, end_line=2)
    assert read.content == 'two\r\n'
    assert read.total_lines == 3


def test_read_truncated_utf8_is_not_reported_as_binary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 'ñ' is two bytes; a byte limit that lands between them must not turn a
    # text file into base64.
    Path('a.txt').write_text('aaaaaaaaaañbbbbbbbbbb')
    tools = _tools()
    read = tools['read_file'](path='a.txt', max_bytes=11)
    assert read.binary is False
    assert read.encoding == 'utf-8'
    assert read.truncated is True
    assert read.content == 'aaaaaaaaaa'


def test_patch_rejects_path_outside_workspace(tmp_path, monkeypatch):
    """`..` cannot be used to leave the allowed trees."""
    _scratch(monkeypatch, tmp_path)
    # The sandbox of the test lives under `/tmp`, so the workspace would be
    # inside the POSIX temp tree. Emptying it isolates the rule being tested:
    # the allowed trees are the workspace and `scratch`, and nothing else.
    monkeypatch.setattr(paths, '_POSIX_TEMP_DIRS', ())
    tools = _tools()

    with pytest.raises(FileToolError, match='outside the workspace'):
        tools['read_file'](path='../outside.txt')


# --- the OS temporary directory ----------------------------------------------
#
# Scratch files are the one place outside the workspace the tools may touch: a
# command writes them, the model reads them back, and refusing the path turns a
# trivial workflow into a round trip through the shell.


def _scratch(monkeypatch, tmp_path) -> Path:
    """Points ``tempfile`` at a sandbox and leaves the workspace elsewhere."""
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(tempfile, 'tempdir', str(scratch))
    return scratch


def test_write_file_creates_a_file_in_the_os_temp_directory(tmp_path, monkeypatch):
    scratch = _scratch(monkeypatch, tmp_path)
    tools = _tools()

    result = tools['write_file'](path=str(scratch / 'notes.txt'), content='scratch\n')

    assert result.created is True
    assert result.appended is False
    assert (scratch / 'notes.txt').read_text() == 'scratch\n'


def test_write_file_creates_parent_directories_in_the_os_temp_directory(tmp_path, monkeypatch):
    scratch = _scratch(monkeypatch, tmp_path)
    tools = _tools()

    tools['write_file'](path=str(scratch / 'deep' / 'dir' / 'a.txt'), content='x')

    assert (scratch / 'deep' / 'dir' / 'a.txt').read_text() == 'x'


def test_read_file_reads_a_file_in_the_os_temp_directory(tmp_path, monkeypatch):
    scratch = _scratch(monkeypatch, tmp_path)
    (scratch / 'log.txt').write_text('one\ntwo\n')
    tools = _tools()

    assert tools['read_file'](path=str(scratch / 'log.txt')).content == 'one\ntwo\n'
    assert tools['read_file'](path=str(scratch / 'log.txt'), start_line=2, end_line=2).content == 'two\n'


def test_patch_file_edits_a_file_in_the_os_temp_directory(tmp_path, monkeypatch):
    scratch = _scratch(monkeypatch, tmp_path)
    (scratch / 'log.txt').write_text('one\ntwo\n')
    tools = _tools()

    result = tools['patch_file'](path=str(scratch / 'log.txt'), old_string='two', new_string='TWO')

    assert result.mode == 'string'
    assert (scratch / 'log.txt').read_text() == 'one\nTWO\n'


def test_the_temp_directory_follows_tmpdir(tmp_path, monkeypatch):
    """``TMPDIR`` is the temp directory of the session, not a hard-coded ``/tmp``."""
    custom = tmp_path / 'custom-tmp'
    custom.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    monkeypatch.setenv('TMPDIR', str(custom))
    monkeypatch.setattr(tempfile, 'tempdir', None)
    # Without this, `/tmp` covers the sandbox and the test proves nothing about
    # `TMPDIR` being honoured.
    monkeypatch.setattr(paths, '_POSIX_TEMP_DIRS', ())
    monkeypatch.chdir(workspace)

    assert resolve_path(str(custom / 'a.txt')) == (custom / 'a.txt').resolve()


def test_the_real_os_temp_directory_is_allowed(tmp_path, monkeypatch):
    """The path a shell redirect lands on has to be usable by the file tools."""
    monkeypatch.chdir(tmp_path)
    real = Path(tempfile.gettempdir()).resolve()

    # Asserted on the roots rather than on a resolved probe: the sandbox of the
    # test is itself under `/tmp` on Linux, which would make a probe succeed for
    # the wrong reason.
    assert real in paths.allowed_roots()
    assert paths.is_allowed(real / 'sigil-file-tools-probe.txt') is True


@pytest.mark.skipif(os.name == 'nt', reason='/tmp is a POSIX convention')
def test_tmp_is_allowed_even_when_tmpdir_points_somewhere_else(tmp_path, monkeypatch):
    """`/tmp` is where a command writes when nobody exports ``TMPDIR``."""
    custom = tmp_path / 'custom-tmp'
    custom.mkdir()
    monkeypatch.setenv('TMPDIR', str(custom))
    monkeypatch.setattr(tempfile, 'tempdir', None)
    monkeypatch.chdir(tmp_path)

    assert resolve_path('/tmp/sigil-file-tools-probe.txt') == Path('/tmp/sigil-file-tools-probe.txt').resolve()


def test_a_sibling_of_the_temp_directory_is_still_refused(tmp_path, monkeypatch):
    """The allowance is the temp tree, not its parent: `..` does not climb out."""
    _scratch(monkeypatch, tmp_path)
    # See above: `tmp_path` lives under `/tmp`, which would otherwise cover the
    # escape this test is about.
    monkeypatch.setattr(paths, '_POSIX_TEMP_DIRS', ())
    tools = _tools()

    with pytest.raises(FileToolError, match='outside the workspace'):
        tools['write_file'](path=str(tmp_path / 'scratch' / '..' / 'escaped.txt'), content='x')
    assert not (tmp_path / 'escaped.txt').exists()


def test_write_creates_parent_directories(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tools = _tools()
    result = tools['write_file'](path='a/b/c.txt', content='x')
    assert result.created is True
    assert Path('a/b/c.txt').read_text() == 'x'


def _agent_with_files():
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    agent = Agent(TestModel(), instructions='x')
    register_file_tools(agent)
    return agent


def test_mutating_file_tools_declare_a_concurrency_guard():
    """Writes are not serialized blindly: they declare when they clash."""
    tools = _agent_with_files()._function_toolset.tools

    # The context the guard needs does not appear in the model schema.
    assert sorted(tools['patch_file'].function_schema.json_schema['properties']) == [
        'end_line',
        'expected_text',
        'new_string',
        'old_string',
        'path',
        'replace_all',
        'start_line',
    ]
    assert sorted(tools['write_file'].function_schema.json_schema['properties']) == ['append', 'content', 'path']

    # No barriers: parallelism stays enabled except for the specific conflict.
    assert tools['patch_file'].sequential is False
    assert tools['write_file'].sequential is False
    assert tools['read_file'].sequential is False


def _run_two_calls(tmp_path, monkeypatch, calls):
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from src.engine.agents.tool_errors import ToolErrorRecovery

    monkeypatch.chdir(tmp_path)

    def model_function(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart(name, args) for name, args in calls])
        return ModelResponse(parts=[TextPart('ok')])

    agent = Agent(FunctionModel(model_function), instructions='x', capabilities=[ToolErrorRecovery()])
    register_file_tools(agent)
    return asyncio.run(agent.run('do both things'))


def _tool_returns(result):
    from pydantic_ai.messages import ToolReturnPart

    return [
        part
        for message in result.all_messages()
        for part in getattr(message, 'parts', [])
        if isinstance(part, ToolReturnPart)
    ]


def test_two_patches_on_the_same_file_conflict(tmp_path, monkeypatch):
    """The first goes through; the second fails instead of stomping on it silently."""
    Path(tmp_path / 'a.txt').write_text('one\ntwo\nthree\n')

    result = _run_two_calls(
        tmp_path,
        monkeypatch,
        [
            ('patch_file', {'path': 'a.txt', 'old_string': 'one', 'new_string': 'ONE'}),
            ('patch_file', {'path': 'a.txt', 'old_string': 'two', 'new_string': 'TWO'}),
        ],
    )

    assert Path(tmp_path / 'a.txt').read_text() == 'ONE\ntwo\nthree\n'
    returns = _tool_returns(result)
    assert [part.outcome for part in returns] == ['success', 'failed']
    assert 'already edits' in str(returns[1].content)
    assert 'separate step' in str(returns[1].content)


def test_patch_and_write_on_the_same_file_conflict(tmp_path, monkeypatch):
    Path(tmp_path / 'a.txt').write_text('one\n')

    result = _run_two_calls(
        tmp_path,
        monkeypatch,
        [
            ('write_file', {'path': 'a.txt', 'content': 'new\n'}),
            ('patch_file', {'path': 'a.txt', 'old_string': 'new', 'new_string': 'other'}),
        ],
    )

    assert Path(tmp_path / 'a.txt').read_text() == 'new\n'
    assert [part.outcome for part in _tool_returns(result)] == ['success', 'failed']


def test_patches_on_different_files_run_in_parallel(tmp_path, monkeypatch):
    Path(tmp_path / 'a.txt').write_text('one\n')
    Path(tmp_path / 'b.txt').write_text('two\n')

    result = _run_two_calls(
        tmp_path,
        monkeypatch,
        [
            ('patch_file', {'path': 'a.txt', 'old_string': 'one', 'new_string': 'ONE'}),
            ('patch_file', {'path': 'b.txt', 'old_string': 'two', 'new_string': 'TWO'}),
        ],
    )

    assert Path(tmp_path / 'a.txt').read_text() == 'ONE\n'
    assert Path(tmp_path / 'b.txt').read_text() == 'TWO\n'
    assert [part.outcome for part in _tool_returns(result)] == ['success', 'success']


def test_reading_a_file_does_not_conflict_with_editing_it(tmp_path, monkeypatch):
    Path(tmp_path / 'a.txt').write_text('one\n')

    result = _run_two_calls(
        tmp_path,
        monkeypatch,
        [
            ('read_file', {'path': 'a.txt'}),
            ('patch_file', {'path': 'a.txt', 'old_string': 'one', 'new_string': 'ONE'}),
        ],
    )

    assert Path(tmp_path / 'a.txt').read_text() == 'ONE\n'
    assert [part.outcome for part in _tool_returns(result)] == ['success', 'success']


def test_external_access_follows_current_session_for_all_file_tools(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(paths, 'temp_dirs', lambda: ())
    external = tmp_path / 'outside.txt'
    external.write_text('before')
    allowed = False
    agent = DummyAgent()
    register_file_tools(agent, allow_external=lambda: allowed)
    tools = agent.tools

    for name, arguments in (
        ('read_file', {}),
        ('write_file', {'content': 'new'}),
        ('patch_file', {'old_string': 'before', 'new_string': 'after'}),
    ):
        with pytest.raises(FileToolError):
            tools[name](path=str(external), **arguments)
    allowed = True
    assert tools['read_file'](path=str(external)).content == 'before'
    tools['patch_file'](path=str(external), old_string='before', new_string='after')
    tools['write_file'](path=str(external), content='new')
    assert external.read_text() == 'new'
    allowed = False
    with pytest.raises(FileToolError):
        tools['read_file'](path=str(external))
