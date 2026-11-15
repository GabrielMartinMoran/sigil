"""Private, bounded lifecycle diagnostics, never conversation or exception text."""

from __future__ import annotations

import faulthandler
import json
import os
import stat
import threading
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from src.engine.paths import service_dir

MAX_LOG_BYTES = 2 * 1024 * 1024
# Uvicorn's Python signal handler can record while this thread is already logging.
_lock = threading.RLock()


def _open_private(path: Path) -> int:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError('Diagnostics require a regular file')
        os.fchmod(descriptor, 0o600)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def record(event: str, *, error: BaseException | None = None, **facts: str | float | bool | None) -> None:
    """Write controlled facts and frame locations; logging must not break recovery."""
    entry: dict[str, object] = {'time': datetime.now(UTC).isoformat(), 'pid': os.getpid(), 'event': event, **facts}
    if error is not None:
        entry['error_type'] = type(error).__name__
        entry['frames'] = [
            {'file': frame.filename, 'line': frame.lineno, 'function': frame.name}
            for frame in traceback.extract_tb(error.__traceback__)[-64:]
        ]
    try:
        directory = service_dir() / 'diagnostics'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink():
            return
        path = directory / f'process-{os.getpid()}.jsonl'
        with _lock:
            if path.exists() and path.stat().st_size >= MAX_LOG_BYTES:
                path.replace(path.with_suffix('.previous.jsonl'))
            with os.fdopen(_open_private(path), 'a', encoding='utf-8') as output:
                output.write(json.dumps(entry, ensure_ascii=True) + '\n')
    except OSError:
        pass


@contextmanager
def diagnose_server() -> Iterator[None]:
    """Record startup/exit and fatal Python stacks without replacing signal handlers."""
    record('server_process_start')
    fault_output = None
    try:
        if not faulthandler.is_enabled():
            try:
                directory = service_dir() / 'diagnostics'
                if not directory.is_symlink():
                    fault_output = os.fdopen(_open_private(directory / f'fault-{os.getpid()}.log'), 'a')
                    faulthandler.enable(file=fault_output, all_threads=True)
            except OSError:
                pass
        yield
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) else 0 if error.code is None else 1
        record('server_exit_requested', exit_code=code)
        raise
    except KeyboardInterrupt:
        record('server_keyboard_interrupt')
        raise
    except BaseException as error:
        record('server_process_exception', error=error)
        raise
    finally:
        record('server_process_exit')
        if fault_output is not None:
            faulthandler.disable()
            fault_output.close()
