"""A session that was never stored."""

from __future__ import annotations


class SessionNotStored(LookupError):
    """The session exists only in memory, so there is no durable row to project.

    ``Composition`` opens a *draft* session — an id and a workspace with nothing
    saved under it yet — until the first message gives it a title and a row. Asking
    for its transcript is a legitimate question with a legitimate answer, "nothing
    yet", and it is not a server failure: the route answers a typed 404 that a client
    reads as an empty conversation. It stays a `LookupError` so every caller that
    already treats a missing row that way keeps working.
    """
