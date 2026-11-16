"""The composition root: everything a client of Sigil needs, built once.

The engine is UI-agnostic and the application is not: the turn loop, the session,
the tree, the model router and the tools have to be wired together in one place,
and that place is here. The terminal client calls this and hands the result to
`SigilApp`; the web server calls the same thing and drives it from an event loop
with no widgets at all.

Two rules make that possible:

- **Nothing reads the environment twice.** `run()` (and the server entry point)
  loads `.env` and `load_settings()` once and passes the values down, so importing
  this module cannot freeze a setting.
- **The client owns its sinks.** The tool logger, the question hub and the
  workspace manager are passed in when they exist, so a client that paints tool
  rows or answers questions can attach itself without the core knowing how.

The object is a record of what was built, not a behaviour: the turn loop lives in
the client (`ui/turn_mixin.py` for the terminal, `server/runtime.py` for the web),
because it is the same decisions written twice for two transports and merging them
is the work the architecture page still has pending.
"""

from __future__ import annotations
