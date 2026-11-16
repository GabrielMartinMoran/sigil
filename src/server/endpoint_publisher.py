"""Publishes the endpoint file that tells clients how to reach a live server."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from src.core.config.paths import endpoint_file
from src.server.endpoint import ENDPOINT_FILE_MODE, TOKEN_BYTES, Endpoint
from src.server.endpoint_file import _remove_quietly, ensure_service_dir, read_endpoint


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
