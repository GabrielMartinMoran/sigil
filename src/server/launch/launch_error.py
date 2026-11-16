"""The service is not reachable and this call did not leave one running."""

from __future__ import annotations


class LaunchError(RuntimeError):
    """The service is not reachable and this call did not leave one running."""

    code = 'launch_failed'
    """Stable identifier, for a client that reports the failure without parsing prose."""
