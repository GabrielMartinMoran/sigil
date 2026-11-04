"""Names and limits of the workspace instruction files."""

from __future__ import annotations

from typing import Literal

AGENTS_FILENAME = 'AGENTS.md'
FALLBACK_FILENAME = 'CLAUDE.md'

MAX_FILE_CHARS = 32_000
"""Cap per file. A 40 KB `AGENTS.md` eats the window."""

MAX_TOTAL_CHARS = 64_000
"""Cap of the whole block, adding up every level of the tree."""

InstructionChangePolicy = Literal['rewrite', 'append']
