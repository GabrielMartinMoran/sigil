"""Tests for the `run` tool / `execute_command`.

They need no network and no LLM: they only run finite system commands.
"""

import asyncio
import os
import time

import pytest

from src.engine.shell.guard import SELF_GUARD_ENV
from src.engine.shell.limits import MAX_OUTPUT_BYTES
from src.engine.shell.runner import execute_command, execute_commands
from src.engine.shell.tools import register_shell_tools

# --- 1. Simple command --------------------------------------------------------


def test_simple_command_pwd():
    result = execute_command('pwd')
    assert result.returncode == 0
    assert result.stdout.strip() == os.getcwd()
    assert not result.timed_out


# --- 2. Command with arguments ------------------------------------------------


def test_command_with_arguments(tmp_path):
    (tmp_path / 'a.txt').write_text('hi')
    result = execute_command(f'ls -la {tmp_path}')
    assert result.returncode == 0
    assert 'a.txt' in result.stdout


# --- 3. Paths with spaces -----------------------------------------------------


def test_path_with_spaces(tmp_path):
    spaced = tmp_path / 'dir with spaces'
    spaced.mkdir()
    (spaced / 'file name.txt').write_text('data')
    result = execute_command(f'ls "{spaced}"')
    assert result.returncode == 0
    assert 'file name.txt' in result.stdout


# --- 4. stdout and stderr capture ---------------------------------------------


def test_captures_stdout_and_stderr_separately():
    result = execute_command('echo out; echo err >&2')
    assert result.returncode == 0
    assert result.stdout.strip() == 'out'
    assert result.stderr.strip() == 'err'


# --- 5. Exit code -------------------------------------------------------------


def test_exit_code_is_returned():
    result = execute_command('exit 3')
    assert result.returncode == 3
    assert not result.timed_out


# --- 6. Nonexistent command ---------------------------------------------------


def test_nonexistent_command_does_not_raise():
    result = execute_command('this_command_does_not_exist_12345')
    assert result.returncode == 127
    assert result.stderr
    assert not result.timed_out


# --- 7. A process waiting on stdin does not block ------------------------------


def test_stdin_closed_does_not_block():
    start = time.monotonic()
    result = execute_command('cat', timeout_seconds=5)
    assert not result.timed_out
    assert result.returncode == 0
    assert time.monotonic() - start < 5


# --- 8. Timeout terminates the process ----------------------------------------


def test_timeout_terminates_process():
    start = time.monotonic()
    result = execute_command('sleep 10', timeout_seconds=1)
    assert result.timed_out
    assert result.returncode == 124
    assert time.monotonic() - start < 8


# --- 9. Timeout leaves no live children ---------------------------------------


def test_timeout_kills_children():
    start = time.monotonic()
    result = execute_command('sleep 30 & wait', timeout_seconds=1)
    assert result.timed_out
    # If the process group had not been killed, this would take ~30s.
    assert time.monotonic() - start < 8


# --- 10. Output limit ---------------------------------------------------------


def test_output_is_truncated():
    result = execute_command("head -c 200000 /dev/zero | tr '\\0' 'a'", timeout_seconds=10)
    assert result.stdout_truncated
    assert len(result.stdout.encode('utf-8')) <= MAX_OUTPUT_BYTES


# --- 11. State distinction ----------------------------------------------------


def test_empty_command_is_rejected():
    result = execute_command('   ')
    assert result.returncode == 2
    assert not result.timed_out


def test_timeout_vs_normal_are_distinguishable(monkeypatch):
    from src.engine.shell import runner

    monkeypatch.setattr(runner, '_clamp_timeout', lambda _: 0.05)
    normal = execute_command('true')
    timed = execute_command('sleep 10', timeout_seconds=1)
    assert not normal.timed_out and normal.returncode == 0
    assert timed.timed_out and timed.returncode == 124


# --- 12. Integration with the agent -------------------------------------------


class _DummyAgent:
    def __init__(self):
        self.registered = {}
        self.options: dict[str, dict[str, object]] = {}

    def tool_plain(self, func, **options):
        self.registered[func.__name__] = func
        self.options[func.__name__] = options
        return func


def test_register_shell_tools_registers_run():
    agent = _DummyAgent()
    register_shell_tools(agent)

    assert 'run' in agent.registered
    result = asyncio.run(agent.registered['run'](['pwd']))
    assert len(result) == 1
    assert result[0].returncode == 0


# --- Self-protection ----------------------------------------------------------


def test_child_process_gets_guard_env():
    result = execute_command(f'echo "${SELF_GUARD_ENV}"')
    assert result.returncode == 0
    assert result.stdout.strip() == '1'


def test_refuses_self_invocation():
    result = execute_command('./sigil')
    assert result.returncode == 126
    assert 'nested Sigil' in result.stderr


def test_refuses_uv_run_main():
    result = execute_command('uv run ./main.py')
    assert result.returncode == 126


# --- 13. Parallel execution ----------------------------------------------------


def test_execute_commands_runs_in_parallel(monkeypatch):
    from threading import Barrier

    from src.engine.shell import runner
    from src.engine.shell.runner import RunToolOutput

    barrier = Barrier(3, timeout=3)

    def command(_cmd, timeout_seconds):
        barrier.wait()
        return RunToolOutput(returncode=0)

    monkeypatch.setattr(runner, 'execute_command', command)
    results = asyncio.run(execute_commands(['first', 'second', 'third']))
    assert len(results) == 3
    assert all(result.returncode == 0 for result in results)


def test_execute_commands_preserve_order():
    results = asyncio.run(execute_commands(['echo first', 'echo second', 'echo third']))
    assert [r.stdout.strip() for r in results] == ['first', 'second', 'third']


def test_execute_commands_errors_do_not_break_others():
    results = asyncio.run(execute_commands(['exit 3', 'echo ok', 'this_command_does_not_exist_12345']))
    assert results[0].returncode == 3
    assert results[1].returncode == 0
    assert results[1].stdout.strip() == 'ok'
    assert results[2].returncode == 127


def test_execute_commands_empty_list():
    assert asyncio.run(execute_commands([])) == []


@pytest.mark.parametrize('requested, expected', [(-1, 1), (0, 1), (1, 1), (60, 60), (120, 120), (121, 120)])
def test_timeout_policy_clamps_to_the_public_bounds(requested: int, expected: int) -> None:
    from src.engine.shell.runner import _clamp_timeout

    assert _clamp_timeout(requested) == expected


def test_execute_commands_timeout_is_per_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine.shell import runner

    # Keep real processes and deadline handling; the public one-second minimum is
    # covered separately, including the unpatched termination tests above.
    monkeypatch.setattr(runner, '_clamp_timeout', lambda _: 0.1)
    start = time.monotonic()
    results = asyncio.run(execute_commands(['sleep 10', 'echo ok'], timeout_seconds=1))
    elapsed = time.monotonic() - start
    assert results[0].timed_out and results[0].returncode == 124
    assert results[1].returncode == 0 and results[1].stdout.strip() == 'ok'
    assert elapsed < 8


def test_cancelled_admission_does_not_start_a_late_process() -> None:
    from src.engine.shell.running import RunningCommands

    owner = RunningCommands()
    generation = owner.generation()
    assert owner.kill_all() == 0
    result = execute_command('echo must-not-start', owner=owner, generation=generation)
    assert result.returncode != 0
    assert not result.stdout
    assert result.stderr is not None
    assert 'cancelled' in result.stderr.lower()
    assert execute_command('echo next', owner=owner).stdout.strip() == 'next'
