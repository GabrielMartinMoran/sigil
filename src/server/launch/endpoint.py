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

from dataclasses import dataclass

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


__all__ = [
    'ENDPOINT_FILE_MODE',
    'SERVICE_DIR_MODE',
    'Endpoint',
]
