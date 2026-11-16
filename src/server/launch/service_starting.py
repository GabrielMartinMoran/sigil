"""Another process owns the store and has not published yet."""

from __future__ import annotations

from src.server.launch.launch_error import LaunchError


class ServiceStarting(LaunchError):
    """Another process owns the store and has not published within the deadline.

    Nothing is wrong: someone else won, and the wait was too short for it. The
    caller retries.
    """

    code = 'service_starting'
