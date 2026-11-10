"""Recover exported variables from the user's shell without exposing their values."""

from __future__ import annotations

import json
import logging
import os
import pwd
import selectors
import shlex
import signal
import subprocess
import sys
import threading
import time

from src.engine.shell.guard import SELF_GUARD_ENV

STARTUP_TIMEOUT = 5.0
_MAX_BYTES = 1024 * 1024
_MARKER = b'\x00SIGIL_SHELL_ENVIRONMENT\x00'
_lock = threading.Lock()
_cache: dict[str, str] | None = None
_logger = logging.getLogger(__name__)


def merge_path(inherited: str, discovered: str) -> str:
    """Preserve inherited path priority while appending new shell entries."""
    return os.pathsep.join(dict.fromkeys((inherited + os.pathsep + discovered).split(os.pathsep)))


def _capture(shell: str) -> dict[str, str]:
    code = 'import json,os,sys; sys.stdout.buffer.write(' + repr(_MARKER) + '+json.dumps(dict(os.environ)).encode())'
    command = f'exec {shlex.quote(sys.executable)} -c {shlex.quote(code)}'
    process = subprocess.Popen(
        [shell, '-ilc', command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, SELF_GUARD_ENV: '1'},
    )
    assert process.stdout is not None
    output = bytearray()
    deadline = time.monotonic() + STARTUP_TIMEOUT
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError('shell initialization timed out')
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > _MAX_BYTES:
                    raise ValueError('shell initialization output exceeded limit')
        if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            raise ValueError('shell initialization failed')
        _, marker, data = bytes(output).rpartition(_MARKER)
        if not marker:
            raise ValueError('shell environment missing')
        value = json.loads(data)
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            raise ValueError('invalid shell environment')
        return value
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()


def account_shell() -> str:
    """Find the account's login shell when the launching process did not provide one."""
    try:
        return pwd.getpwuid(os.getuid()).pw_shell
    except KeyError, OSError:
        return ''


def shell_environment() -> dict[str, str]:
    """Capture once per process; unavailable or failing shells leave the inherited environment intact."""
    global _cache
    with _lock:
        if _cache is None:
            shell = os.environ.get('SHELL') or account_shell()
            _cache = {}
            if shell:
                try:
                    _cache = _capture(shell)
                except OSError, ValueError, TimeoutError, subprocess.TimeoutExpired:
                    _logger.warning('Could not load the user shell environment; using the inherited environment.')
        return _cache.copy()


def child_environment(extra_env: dict[str, str] | None) -> dict[str, str]:
    """Merge shell exports, inherited values, and tool-specific safety settings."""
    discovered = shell_environment()
    inherited = dict(os.environ)
    merged = {**discovered, **inherited}
    if 'PATH' in inherited and 'PATH' in discovered:
        merged['PATH'] = merge_path(inherited['PATH'], discovered['PATH'])
    merged.update(extra_env or {})
    merged.update({'CI': '1', 'GIT_TERMINAL_PROMPT': '0', SELF_GUARD_ENV: '1'})
    return merged


def reset_cache() -> None:
    """Clear the cached shell environment, primarily for isolated tests."""
    global _cache
    with _lock:
        _cache = None
