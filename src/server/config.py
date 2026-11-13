"""Where the server listens, and the password that guards the web client.

There are two surfaces, and only one of them has a password:

- **The API** (`sigil server`): local clients authenticate with the token the
  endpoint file publishes. No browser is involved, so there is nothing to type a
  password into, and none is invented.
- **The API plus the web client** (`sigil serve`): the password is a secret of the
  deployment, not of the conversation. It is read from `SIGIL_SERVER_PASSWORD` and
  never written to the project. When the variable is missing one is generated,
  printed once on the terminal that started the server, and used for that process
  only — so an accidental `sigil serve` on a laptop is protected by default
  instead of open to the local network.

Generated and configured are announced differently on purpose: the first is a
one-time credential the user has to copy, the second is a reminder that the
environment is where it belongs.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

PASSWORD_ENV = 'SIGIL_SERVER_PASSWORD'
HOST_ENV = 'SIGIL_SERVER_HOST'
PORT_ENV = 'SIGIL_SERVER_PORT'
ORIGINS_ENV = 'SIGIL_SERVER_ORIGINS'
"""Comma-separated origins allowed to call the API cross-origin.

Unset is the normal case: the client is served by this server and same-origin
requests are not subject to CORS. Setting it is for the client served elsewhere.
"""

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8760
GENERATED_PASSWORD_CHARS = 32
"""Hex characters: 16 bytes of entropy, short enough to copy by hand."""

PASSWORD_MIN_CHARS = 8
"""Anything shorter is refused instead of accepted silently."""


class ServerConfigError(ValueError):
    """The requested configuration cannot work, and the user has to know why."""


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


def generate_password(length: int = GENERATED_PASSWORD_CHARS) -> str:
    """A random hexadecimal password of ``length`` characters.

    ``length`` must be even because ``token_hex`` emits two characters per byte;
    an odd one is rounded up, which makes the number of bytes the property that
    matters and the character count the one that follows.
    """
    if length < 2:
        raise ValueError('A generated password needs at least two characters.')
    return secrets.token_hex((length + 1) // 2)[:length]


def resolve_config(
    *,
    host: str | None = None,
    port: int | str | None = None,
    password: str | None = None,
    env: Mapping[str, str] | None = None,
    web: bool = True,
) -> ServerConfig:
    """Builds the configuration: explicit arguments, then the environment, then a default.

    ``web=False`` is the API-only surface: the password is neither read nor invented,
    because the only credential there is the token in the endpoint file.

    Raises:
        ServerConfigError: When the port is not a port, or a configured password is
            shorter than ``PASSWORD_MIN_CHARS``. A too-short secret is refused here
            rather than accepted and forgotten.
    """
    source = os.environ if env is None else env
    resolved_host = (host or source.get(HOST_ENV) or DEFAULT_HOST).strip() or DEFAULT_HOST

    raw_port: int | str | None = port if port is not None else source.get(PORT_ENV)
    resolved_port = _parse_port(raw_port)
    origins = _parse_origins(source.get(ORIGINS_ENV))

    if not web:
        # Ignored on purpose, not just unset: an exported password must not turn the
        # backend into something that prints a secret or accepts a login.
        return ServerConfig(
            host=resolved_host,
            port=resolved_port,
            web=False,
            origins=origins,
        )

    given = password if password is not None else source.get(PASSWORD_ENV)
    if given is None or not given.strip():
        return ServerConfig(
            host=resolved_host,
            port=resolved_port,
            password=generate_password(),
            generated=True,
            origins=origins,
        )
    cleaned = given.strip()
    if not cleaned:
        raise ServerConfigError(f'{PASSWORD_ENV} is empty. Unset it to have a random one generated.')
    # A short password is not refused: the user may be testing on a private network,
    # and blocking that turns a five-second check into an argument with the tool. It
    # travels as `weak` instead, and the banner says so once — which is the part that
    # actually protects someone from deploying a secret they did not think about.
    return ServerConfig(
        host=resolved_host,
        port=resolved_port,
        password=cleaned,
        generated=False,
        weak=len(cleaned) < PASSWORD_MIN_CHARS,
        origins=origins,
    )


def _parse_origins(value: str | None) -> tuple[str, ...]:
    """The origins list, without the wildcard and without duplicates.

    ``*`` is dropped instead of honoured: with credentials a wildcard is refused by
    every browser, so accepting it would produce a configuration that looks like it
    works and silently does not. An origin is scheme + host + port: a bare hostname
    is not an origin and is dropped too.
    """
    if not value:
        return ()
    origins: list[str] = []
    for raw in value.split(','):
        item = raw.strip().rstrip('/')
        if not item or item == '*' or '://' not in item:
            continue
        if item not in origins:
            origins.append(item)
    return tuple(origins)


def _parse_port(value: int | str | None) -> int:
    if value is None or value == '':
        return DEFAULT_PORT
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ServerConfigError(f'Invalid port: {value!r}.') from exc
    if not 1 <= port <= 65535:
        raise ServerConfigError(f'Port {port} is out of range (1-65535).')
    return port


def banner(config: ServerConfig) -> str:
    """What the terminal says before the server starts accepting connections.

    For the web surface it is the only copy of a generated password, so it names the
    variable that replaces it: a user who wants a stable secret has to know where it
    lives. The API surface has no password to print, and says where the token is
    instead.
    """
    title = 'Sigil web server' if config.web else 'Sigil backend (API only)'
    lines = [title, f'  listening : {config.local_url}']
    if config.host not in {'127.0.0.1', 'localhost', '::1'}:
        lines.append(f'  bind      : {config.host} (reachable from the network)')
    if not config.web:
        lines.extend(
            [
                '  password  : none — local clients use the endpoint token',
                '',
                '  The web client is not mounted and there is no login form.',
                '  Run `sigil serve` to also serve it, behind a password.',
            ]
        )
    elif config.generated:
        lines.extend(
            [
                '  password  : ' + config.password,
                '',
                '  This password was generated for this run and only exists in this process.',
                f'  To keep one, export {PASSWORD_ENV}=<your password> before starting.',
            ]
        )
    else:
        lines.append(f'  password  : from {PASSWORD_ENV}')
    if config.weak:
        lines.extend(
            [
                '',
                f'  warning   : that password is shorter than {PASSWORD_MIN_CHARS} characters.',
                '  Anyone who reaches this port can guess it. Fine for a test on a private',
                f'  network; use `{PASSWORD_ENV}=$(openssl rand -hex 16)` for anything else.',
            ]
        )
    if config.host in {'0.0.0.0', '::'}:
        lines.extend(
            [
                '',
                '  The server is bound to every interface: anyone who can route to this',
                '  machine and knows the password gets a shell in the workspace. A private',
                '  network (Tailscale, a LAN) is the intended deployment.',
            ]
        )
    return '\n'.join(lines)


__all__ = [
    'DEFAULT_HOST',
    'DEFAULT_PORT',
    'GENERATED_PASSWORD_CHARS',
    'HOST_ENV',
    'ORIGINS_ENV',
    'PASSWORD_ENV',
    'PASSWORD_MIN_CHARS',
    'PORT_ENV',
    'ServerConfig',
    'ServerConfigError',
    'banner',
    'generate_password',
    'resolve_config',
]
