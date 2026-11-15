"""One cooperating server per database, enforced by the operating system.

The lock is an advisory ``flock`` on a file next to the database. It is taken
before reconciliation and held until the process exits, so a second server either
owns the store or refuses to start. It does not stop legacy processes that never
ask for the lock; the boundary is cooperation, and it is named that way.
"""

from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path
from typing import Self

OWNER_PID_PREFIX = 'pid='
"""First field ``acquire`` writes. ``owner_pid`` reads it back."""

OWNER_RECORD_BYTES = 256
"""A bounded read of that record: two numbers and a newline, and a cap on nonsense."""


class OwnershipError(RuntimeError):
    """Another server owns the store, or the lock path is not safe to use."""


class ServerOwnership:
    """An exclusive, stable lock for one database.

    The file is opened without following symbolic links and is never unlinked:
    removing it would let two processes lock two different inodes at the same
    time. ``acquire`` fails closed when the path is a link, is not a regular
    file, or belongs to another user.
    """

    def __init__(self, database_path: str | Path) -> None:
        database = Path(database_path)
        self.path = database.with_name(f'{database.name}.server.lock')
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        """Takes the lock, or raises :class:`OwnershipError` without working."""
        if self._fd is not None:
            return
        fd = self._open_for_locking()
        try:
            info = os.fstat(fd)
            if not _is_regular(info.st_mode):
                raise OwnershipError(f'The server lock {self.path} is not a regular file')
            if hasattr(os, 'getuid') and info.st_uid != os.getuid():
                raise OwnershipError(f'The server lock {self.path} belongs to another user')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                owner = _describe_owner(self.path)
                raise OwnershipError(f'Another Sigil server owns {self.path}{owner}') from exc
            os.ftruncate(fd, 0)
            os.write(fd, f'{OWNER_PID_PREFIX}{os.getpid()} started={time.time():.0f}\n'.encode('ascii'))
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def free(self) -> bool:
        """Whether nobody owns the store, asked without becoming an owner.

        It locks, unlocks by closing, and never writes: a question must not overwrite
        the answer. The ``pid=`` record is the last *real* holder's, and the launcher
        needs it intact to tell "the owner died" from "the owner is between two
        steps" — a probe that recorded itself would destroy exactly that evidence.

        Raises:
            OwnershipError: when the lock path is not safe to use at all, which is a
                configuration failure rather than an occupied store.
        """

        if self._fd is not None:
            return False
        fd = self._open_for_locking()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        finally:
            # Closing is also unlocking. Nothing is written, so the holder this
            # question found — if any — is still the one `owner_pid` reports.
            os.close(fd)
        return True

    def release(self) -> None:
        fd = self._fd
        if fd is None:
            return
        self._fd = None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def owner_pid(self) -> int | None:
        """The pid the last holder recorded, when it can still be read.

        ``acquire`` writes the record and the file is never unlinked, so it outlives
        its holder: a caller can read it to ask whether the process that last owned
        the store still exists. ``None`` means the record is absent, unreadable or
        not a pid, and callers must treat that as *unknown* rather than as death.
        The file is opened without following a link, like every other read of it.
        """

        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
        try:
            descriptor = os.open(self.path, flags)
        except OSError:
            return None
        try:
            record = os.read(descriptor, OWNER_RECORD_BYTES).decode('ascii', 'replace')
        except OSError:
            return None
        finally:
            os.close(descriptor)
        for line in record.splitlines():
            if not line.startswith(OWNER_PID_PREFIX):
                continue
            try:
                return int(line[len(OWNER_PID_PREFIX) :].split()[0])
            except IndexError, ValueError:
                return None
        return None

    def _open_for_locking(self) -> int:
        """The lock file, opened for locking, refusing a path that is not safe to use."""

        self._reject_unsafe_path()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
        try:
            return os.open(self.path, flags, 0o600)
        except OSError as exc:
            raise OwnershipError(f'Cannot open the server lock {self.path}: {exc}') from exc

    def _reject_unsafe_path(self) -> None:
        try:
            info = os.lstat(self.path)
        except FileNotFoundError:
            return
        if not _is_regular(info.st_mode):
            raise OwnershipError(f'The server lock {self.path} is not a safe regular file')

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def _is_regular(mode: int) -> bool:
    import stat

    return stat.S_ISREG(mode)


def _describe_owner(path: Path) -> str:
    """The pid recorded by the holder, when it can be read; empty otherwise."""
    try:
        text = path.read_text(encoding='ascii', errors='replace').strip()
    except OSError:
        return ''
    return f' ({text})' if text else ''


__all__ = ['OwnershipError', 'ServerOwnership']
