"""Reading and writing the endpoint file."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from src.core.config.paths import endpoint_file
from src.server.launch.endpoint import GROUP_AND_OTHER, SERVICE_DIR_MODE, Endpoint


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
