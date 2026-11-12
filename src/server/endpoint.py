"""The rendezvous file: how a local client finds the service, and what it must not trust.

Three decisions are worth writing down, because each one is a refusal of a
simpler design.

- **TCP on loopback, not a UNIX socket.** uvicorn creates a UNIX socket with mode
  ``0666`` regardless of the umask (see ``uvicorn.server.Server.startup``), so a
  socket cannot isolate one user from another and claiming otherwise would be a
  false sense of isolation. A socket on a Windows-mounted path under WSL does not
  work at all. TCP plus a token in a ``0600`` file puts the barrier somewhere it
  can actually be enforced.
- **Publication is atomic.** The payload is written to a sibling temporary file
  and moved with ``os.replace``, so a client reading at the same moment sees the
  previous publication or the next one, never a truncated file. A truncated read
  would look like a corrupt endpoint and be ignored, which is safe but useless.
- **The file is data, not authority.** Every reader re-checks the JSON, the
  fields and the permissions, and treats a stale, corrupt, world-readable or
  symlinked file as absent. A client that trusted the file would connect to a
  port that is no longer this service and hand it the token.

The token is generated once per run with :mod:`secrets`. It is not derived from
the password and it is never a substitute for it: the web client logs in with the
cookie, a local client sends ``Authorization: Bearer``.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.engine.paths import endpoint_file

SERVICE_DIR_MODE = 0o700
"""Readable, writable and searchable by its owner only. The token lives one level down."""

ENDPOINT_FILE_MODE = 0o600
"""The token is in the file, so the file is the secret. Nothing else may read it."""

GROUP_AND_OTHER = 0o077
"""Any of these bits means someone other than the owner can read the token."""

TOKEN_BYTES = 32
"""Entropy behind the token; ``token_urlsafe`` turns it into 43 characters."""


@dataclass(frozen=True, slots=True)
class Endpoint:
    """What a client needs to reach the service that published it."""

    host: str
    port: int
    pid: int
    token: str
    started_at: float

    def to_payload(self) -> dict[str, object]:
        """The file's contract, in one place so reading and writing cannot drift."""
        return {
            'host': self.host,
            'port': self.port,
            'pid': self.pid,
            'token': self.token,
            'started_at': self.started_at,
        }


def ensure_service_dir(path: Path) -> Path:
    """Creates the service directory ``0700``, narrowing it if it already exists wider.

    ``mkdir``'s mode is filtered by the umask, which can only remove bits, so
    ``0700`` never comes out wider than asked. The explicit ``chmod`` is what
    makes that a property of this code instead of a property of the caller's
    shell, and it repairs a directory some earlier run left readable.
    """

    path.mkdir(mode=SERVICE_DIR_MODE, parents=True, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(f'The service directory {path} is not a directory')
    if info.st_mode & GROUP_AND_OTHER:
        os.chmod(path, SERVICE_DIR_MODE)
    return path


def read_endpoint(path: Path | None = None) -> Endpoint | None:
    """The endpoint this data directory publishes, or ``None`` when it is not trustworthy.

    Absent, stale, truncated, readable by someone else, owned by someone else and
    reached through a link all get the same answer: there is no endpoint here.
    """

    target = endpoint_file() if path is None else Path(path)
    try:
        info = os.lstat(target)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_mode & GROUP_AND_OTHER:
        return None
    if hasattr(os, 'getuid') and info.st_uid != os.getuid():
        return None
    try:
        payload = json.loads(target.read_text(encoding='utf-8'))
    except OSError, ValueError:
        return None
    return _endpoint_from(payload)


def _endpoint_from(payload: object) -> Endpoint | None:
    if not isinstance(payload, dict):
        return None
    host = payload.get('host')
    token = payload.get('token')
    started_at = payload.get('started_at')
    port = _integer(payload.get('port'))
    pid = _integer(payload.get('pid'))
    if not isinstance(host, str) or not host.strip():
        return None
    if not isinstance(token, str) or not token:
        return None
    if isinstance(started_at, bool) or not isinstance(started_at, (int, float)):
        return None
    if port is None or not 1 <= port <= 65535:
        return None
    if pid is None or pid < 1:
        return None
    return Endpoint(host=host, port=port, pid=pid, token=token, started_at=float(started_at))


def _integer(value: object) -> int | None:
    """An integer, refusing ``True`` and ``1.0``: a boolean or a float is not a port."""

    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


class EndpointPublisher:
    """Writes, and later removes, the file that names this service."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        token: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = endpoint_file() if path is None else Path(path)
        self.token = token if token else secrets.token_urlsafe(TOKEN_BYTES)
        self._clock = clock
        self._published: Endpoint | None = None

    def publish(self, host: str, port: int) -> Endpoint:
        """Publishes the endpoint, replacing any previous one in a single step."""

        endpoint = Endpoint(
            host=host,
            port=port,
            pid=os.getpid(),
            token=self.token,
            started_at=round(self._clock(), 3),
        )
        ensure_service_dir(self.path.parent)
        temporary = self._write_temporary(endpoint)
        try:
            os.replace(temporary, self.path)
        except BaseException:
            _remove_quietly(temporary)
            raise
        self._published = endpoint
        return endpoint

    def clear(self) -> None:
        """Unpublishes this run, leaving a file another run already replaced.

        A process that loses the ownership lock, or one that shuts down later than
        it thinks, must not delete the endpoint of the service that succeeded it:
        its clients would be left with a file that names nothing.
        """

        published, self._published = self._published, None
        if published is None:
            return
        current = read_endpoint(self.path)
        if current is not None and current.token != published.token:
            return
        _remove_quietly(self.path)

    def _write_temporary(self, endpoint: Endpoint) -> Path:
        descriptor, name = tempfile.mkstemp(dir=self.path.parent, prefix=f'{self.path.name}.', suffix='.tmp')
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                # mkstemp already asks for 0600; setting it again is what makes the
                # mode independent of the platform's default.
                os.fchmod(handle.fileno(), ENDPOINT_FILE_MODE)
                handle.write(json.dumps(endpoint.to_payload()))
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            _remove_quietly(temporary)
            raise
        return temporary


def _remove_quietly(path: Path) -> None:
    """Removes a file nobody can act on the absence of.

    The publisher's temporary file is garbage if the write failed, and the
    endpoint is ignored by every reader once it is gone: an ``OSError`` here would
    turn a shutdown into a traceback for no benefit.
    """

    try:
        path.unlink()
    except OSError:
        return


__all__ = [
    'ENDPOINT_FILE_MODE',
    'SERVICE_DIR_MODE',
    'Endpoint',
    'EndpointPublisher',
    'ensure_service_dir',
    'read_endpoint',
]
