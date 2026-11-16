"""A model that refuses to run because nothing has been configured yet.

The agent needs *a* model at construction time, but the first run of the app has
zero provider accounts. Instead of inventing a fake account or shipping a dummy
key, the router starts with this: it satisfies the ``Model`` contract and fails
the moment a request is attempted, with a message that says exactly what to do.

That keeps the invariant "the agent is always constructible" without opening a
path where a request silently goes somewhere unexpected.
"""

from __future__ import annotations
