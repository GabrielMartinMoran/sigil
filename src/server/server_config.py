"""Host, port and password the server runs with."""

from __future__ import annotations

from dataclasses import dataclass

from src.server.server_defaults import DEFAULT_HOST, DEFAULT_PORT


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Host, port and password the server runs with."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    password: str = ''
    generated: bool = False
    """``True`` when the password was invented here and only exists in this process."""

    weak: bool = False
    """``True`` when a configured password is shorter than ``PASSWORD_MIN_CHARS``.

    It does not stop the server: it is announced, once, so a password nobody thought
    about does not become a deployment nobody thought about.
    """

    origins: tuple[str, ...] = ()
    """Origins allowed to call the API cross-origin, from ``SIGIL_SERVER_ORIGINS``.

    Empty in the normal case: the client is served by this same server, and
    same-origin requests are not subject to CORS at all — a different hostname
    (Tailscale, a LAN address) is still the same origin, because the page came from
    there. This exists for the client served *elsewhere*: a Vite dev server on another
    machine, a static host. Never ``*``: a wildcard with cookies is refused by browsers.
    """

    web: bool = True
    """Whether the web client is part of this surface.

    ``False`` is the API alone: no bundle is mounted, no login route exists and no
    password is invented or read. Only ``True`` — `sigil serve` — can ask for one.
    """

    @property
    def local_url(self) -> str:
        """The URL to paste in a browser when the server binds to this machine."""
        host = 'localhost' if self.host in {'0.0.0.0', '::', ''} else self.host
        if ':' in host and not host.startswith('['):
            host = f'[{host}]'
        return f'http://{host}:{self.port}'
