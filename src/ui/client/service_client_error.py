"""The base of every failure a client reports."""

from __future__ import annotations


class ServiceClientError(Exception):
    """The base of every failure this client reports.

    ``code`` is a stable identifier so a caller can branch on the kind without
    parsing prose, the same contract `src/server/launch.py` keeps.
    """

    code = 'service_error'
