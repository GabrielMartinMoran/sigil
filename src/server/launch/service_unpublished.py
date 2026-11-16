"""A child this call started never published an endpoint."""

from __future__ import annotations

from src.server.launch.child import Child
from src.server.launch.launch_error import LaunchError


class ServiceUnpublished(LaunchError):
    """A process this call started is alive and never published an endpoint.

    The child is still running, so it is carried on the error: the caller decides
    whether to keep waiting or to stop it, and nothing here kills a process that
    might be about to succeed.
    """

    code = 'service_unpublished'

    def __init__(self, message: str, *, child: Child) -> None:
        super().__init__(message)
        self.child = child
