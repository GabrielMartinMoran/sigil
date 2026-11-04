"""Tests de las tools de archivos."""

import asyncio
from pathlib import Path

import pytest

from src.engine.files.models.file_tool_error import FileToolError
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
    result = tools['write_file'](path='notes.txt', content='hola\n')
    assert result.created is True
    assert result.bytes_written == 5

    read = tools['read_file'](path='notes.txt')
    assert read.content == 'hola\n'
    assert read.binary is False
    assert read.encoding == 'utf-8'
    assert read.size == 5


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
    monkeypatch.chdir(tmp_path)
    tools = _tools()
    with pytest.raises(FileToolError, match='outside the workspace'):
        tools['read_file'](path='../outside.txt')


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
    """Las escrituras no se serializan de prepo: declaran cuándo chocan."""
    tools = _agent_with_files()._function_toolset.tools

    # El contexto que necesita la guarda no aparece en el schema del modelo.
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

    # Nada de barreras: el paralelismo queda habilitado salvo el conflicto puntual.
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
    return asyncio.run(agent.run('hacé las dos cosas'))


def _tool_returns(result):
    from pydantic_ai.messages import ToolReturnPart

    return [
        part
        for message in result.all_messages()
        for part in getattr(message, 'parts', [])
        if isinstance(part, ToolReturnPart)
    ]


def test_two_patches_on_the_same_file_conflict(tmp_path, monkeypatch):
    """La primera pasa; la segunda falla en vez de pisarla en silencio."""
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
            ('write_file', {'path': 'a.txt', 'content': 'nuevo\n'}),
            ('patch_file', {'path': 'a.txt', 'old_string': 'nuevo', 'new_string': 'otro'}),
        ],
    )

    assert Path(tmp_path / 'a.txt').read_text() == 'nuevo\n'
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
