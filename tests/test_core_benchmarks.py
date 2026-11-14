"""Correctness checks for diagnostic benchmark prototypes, not timing gates."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.benchmark_core import drain_candidate, history, memoized_view, range_candidate, whole_candidate
from src.engine.context.budget import ContextBudget
from src.engine.context.strategy.append_only import AppendOnlyStrategy
from src.engine.files.tools import _read_line_range, _read_whole
from src.engine.shell.runner import execute_command


@pytest.mark.parametrize('data', [b'hello\nworld\n', b'hello\r\nworld', 'multibyte: ñ'.encode(), b'\x00\xffbinary'])
@pytest.mark.parametrize('limit', [1, 12, 1024])
def test_should_preserve_bounded_read_result_when_using_candidate(tmp_path: Path, data: bytes, limit: int) -> None:
    target = tmp_path / 'input'
    target.write_bytes(data)
    assert whole_candidate(target, len(data), limit) == _read_whole(target, len(data), limit)


@pytest.mark.parametrize('text', ['one\ntwo\nthree\n', 'one\r\ntwo\r\nthree', 'one\vtwo\nthree\n'])
def test_should_preserve_valid_range_and_line_count_when_using_candidate(tmp_path: Path, text: str) -> None:
    target = tmp_path / 'input'
    target.write_bytes(text.encode())
    expected = _read_line_range(target, str(target), target.stat().st_size, 1, 2)
    assert range_candidate(target, 1, 2) == (expected.content, expected.total_lines)


@pytest.mark.parametrize('window', [10_000_000, 10_000])
def test_should_preserve_projection_when_memoizing_one_request(window: int) -> None:
    messages = history(20)
    strategy = AppendOnlyStrategy()
    budget = ContextBudget(window=window)
    assert memoized_view(strategy, messages, budget) == strategy.view(messages, budget)


@pytest.mark.parametrize('command', ['printf benchmark', 'head -c 1048576 /dev/zero', 'printf error >&2; exit 3'])
def test_should_preserve_captured_process_output_when_using_bounded_wait(command: str, tmp_path: Path) -> None:
    with patch('src.engine.shell.environment._cache', {}):
        expected = execute_command(command, cwd=tmp_path)
        with patch('src.engine.shell.runner.drain', drain_candidate):
            assert execute_command(command, cwd=tmp_path) == expected


def test_should_timeout_when_process_closes_streams_without_exiting(tmp_path: Path) -> None:
    with patch('src.engine.shell.environment._cache', {}), patch('src.engine.shell.runner.drain', drain_candidate):
        result = execute_command('exec 1>&- 2>&-; sleep 5', timeout_seconds=1, cwd=tmp_path)
    assert result.returncode == 124
    assert result.timed_out
