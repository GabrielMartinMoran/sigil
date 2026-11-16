"""Turns tool errors into failed results the model can read.

Without this, a tool exception (a `patch_file` that does not find the
`old_string`, a `read_file` on a nonexistent path) aborts the whole run:
the turn dies, the user takes back control and the agent never learns
what failed and cannot correct itself.

pydantic-ai lets the exception through by default. `ToolFailed` turns it into a
`ToolReturnPart` with `outcome='failed'` that the model sees on the next step.
Unlike `ModelRetry`, it does not consume the tool's retry budget
nor does it ask the model to repeat the same call: it reports the failure so it
can adapt the plan.
"""

from __future__ import annotations

"""Cap of the message returned to the model, so the context is not inflated."""


__all__ = []
