"""Bounded shutdown of the stdio MCP servers.

Closing a stdio MCP server goes through the SDK's documented escalation: close
its stdin, give it a grace period to exit, and only then signal its process
group. The SDK's grace period is two seconds, and a server that ignores the EOF
makes every quit wait it out — which is what turned closing Sigil into a
multi-second pause.

A well-behaved stdio server exits on EOF in a few milliseconds. The ones that do
not are exactly the case the escalation exists for, and the SDK still goes on to
`SIGTERM` (and then `SIGKILL`) if the shorter grace runs out, so nothing is left
behind.

The value is a policy of this application, not of the SDK, and it is read once at
startup by the composition root, like every other setting.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

ENV_MCP_SHUTDOWN_TIMEOUT = 'SIGIL_MCP_SHUTDOWN_TIMEOUT'

DEFAULT_SHUTDOWN_TIMEOUT = 0.2
"""Seconds a stdio server has to exit on EOF before it is signalled.

Far below the SDK's two seconds: the servers that respect EOF are already gone,
and the ones that do not get the signal they require anyway.
"""


def read_shutdown_timeout(environ: Mapping[str, str] | None = None) -> float:
    """The configured grace period, or the default.

    A malformed value falls back to the default instead of failing startup: it
    tunes how long a quit waits, nothing about what a session does.

    Takes the environment as an argument so a test builds the value it wants; the
    application calls it once, from the composition root.
    """
    env = os.environ if environ is None else environ
    raw = env.get(ENV_MCP_SHUTDOWN_TIMEOUT, '').strip()
    if not raw:
        return DEFAULT_SHUTDOWN_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_SHUTDOWN_TIMEOUT
    return value if value > 0 else DEFAULT_SHUTDOWN_TIMEOUT


def apply_shutdown_timeout(seconds: float) -> float:
    """Sets the SDK's grace period for this process and returns the value applied.

    ``mcp.client.stdio`` keeps it as a module constant and reads it at call time,
    so assigning it here bounds every stdio shutdown in the process — which is the
    point: they all happen during the same quit.

    The value is required: reading it here would put an environment read on the
    loading worker, and the startup path is the one place that reads the process
    environment.
    """
    from mcp.client import stdio

    stdio.PROCESS_TERMINATION_TIMEOUT = seconds
    return seconds


__all__ = [
    'DEFAULT_SHUTDOWN_TIMEOUT',
    'ENV_MCP_SHUTDOWN_TIMEOUT',
    'apply_shutdown_timeout',
    'read_shutdown_timeout',
]
