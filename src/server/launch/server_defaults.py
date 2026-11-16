"""Defaults and environment variable names of the server surface."""

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
