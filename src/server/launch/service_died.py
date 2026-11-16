"""The process starting the service died before publishing."""

from __future__ import annotations

from src.server.launch.launch_error import LaunchError


class ServiceDied(LaunchError):
    """A process was starting the service and released the store without publishing.

    Its own output, when it produced any, is quoted: a bundle check, a taken port
    and a refused nested invocation all end here, and the reason is in that text.
    """

    code = 'service_died'
