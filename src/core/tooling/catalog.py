"""What each tool needs to exist, declared where the tool is registered.

A tool is not free: it occupies the schema the provider serializes on every request,
and the model plans around it. Two things decide whether it belongs in a request:

- **The model.** A route that cannot read images has no use for `view_image`, and
  advertising it invites a call that fails.
- **The agent profile.** A profile that denies `read` must not be told about
  `read_file` — the tool is already filtered out of the definitions, and a prompt that
  still describes it teaches the model to plan around something it cannot do.

Both answers need the same catalogue, so it is built once while the tools are
registered: the name of each tool, the permission it declared, and the model feature
it requires.
"""

from __future__ import annotations
