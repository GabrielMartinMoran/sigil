"""Tests de las tools de archivos."""

import hashlib
from pathlib import Path

import pytest

from src.engine.tools.files import FileToolError, register_file_tools


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


def test_patch_line_mode_validates_sha256(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('a.txt').write_text('one\ntwo\nthree\n')
    tools = _tools()
    actual = hashlib.sha256(b'two\n').hexdigest()
    tools['patch_file'](path='a.txt', start_line=2, end_line=2, new_string='TWO', expected_sha256=actual)
    assert Path('a.txt').read_text() == 'one\nTWO\nthree\n'


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
