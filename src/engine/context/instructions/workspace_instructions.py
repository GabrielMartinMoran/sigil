"""Resolution of the instruction files of the directory tree.

The `workspace` block resolves `AGENTS.md` (or `CLAUDE.md` as a fallback) over the
active directory tree:

- The session root always applies.
- A subdirectory applies only when the agent operates inside it.
- The order is parent -> child: the child goes last and takes precedence.
- In the same directory, `AGENTS.md` wins over `CLAUDE.md`.

It is operator content, not user content: it travels through the instructions channel,
never as a user message.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from pathlib import Path

from src.engine.context.instructions.constants import (
    AGENTS_FILENAME,
    FALLBACK_FILENAME,
    MAX_FILE_CHARS,
    MAX_TOTAL_CHARS,
)
from src.engine.context.instructions.instruction_block import InstructionBlock
from src.engine.context.instructions.reader import CacheEntry, read_cached
from src.engine.context.instructions.workspace_file import WorkspaceFile


class WorkspaceInstructions:
    """Resolves the instruction files of the directory tree."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_file_chars: int = MAX_FILE_CHARS,
        max_total_chars: int = MAX_TOTAL_CHARS,
    ) -> None:
        self._root = Path(os.path.realpath(str(root)))
        self._max_file_chars = max_file_chars
        self._max_total_chars = max_total_chars
        self._cache: dict[Path, CacheEntry] = {}

    @property
    def root(self) -> Path:
        return self._root

    def rebind(self, root: str | Path) -> None:
        """Moves the resolved tree to another workspace and forgets the cached files.

        The cache is keyed by path, so entries of the previous workspace would
        never be reused; keeping them only grows memory across switches.
        """
        target = Path(os.path.realpath(str(root)))
        if target == self._root:
            return
        self._root = target
        self._cache.clear()

    def _read(self, path: Path) -> CacheEntry | None:
        return read_cached(path, max_chars=self._max_file_chars, cache=self._cache)

    def _file_for(self, directory: Path) -> Path | None:
        """`AGENTS.md` if present; otherwise `CLAUDE.md`."""
        for filename in (AGENTS_FILENAME, FALLBACK_FILENAME):
            candidate = directory / filename
            if candidate.is_file():
                return candidate
        return None

    def resolve(self, scopes: Iterable[Path]) -> list[WorkspaceFile]:
        """Instruction files for the given directories, parent -> child."""
        ordered = sorted(set(scopes), key=lambda path: (len(path.parts), str(path)))
        found: list[WorkspaceFile] = []
        total = 0

        for directory in ordered:
            try:
                directory.relative_to(self._root)
            except ValueError:
                continue
            path = self._file_for(directory)
            if path is None:
                continue
            entry = self._read(path)
            if entry is None or not entry.content.strip():
                continue

            remaining = self._max_total_chars - total
            if remaining <= 0:
                break
            content = entry.content
            truncated = entry.truncated
            if len(content) > remaining:
                content = content[:remaining]
                truncated = True
            total += len(content)

            relative = path.relative_to(self._root)
            found.append(
                WorkspaceFile(
                    path=path,
                    relative='.' if str(relative) == '.' else str(relative),
                    content=content,
                    truncated=truncated,
                )
            )
        return found

    def render(self, scopes: Iterable[Path]) -> str:
        """Text of the `workspace` block, ready for the instructions."""
        files = self.resolve(scopes)
        if not files:
            return ''

        lines = [
            '## Workspace instructions',
            '',
            'The following files were found in the working directory tree. They are',
            'operator-authored project instructions. More specific files (deeper in the',
            'tree) take precedence over more general ones when they conflict.',
            '',
        ]
        for file in files:
            lines.append(f'### `{file.relative}`')
            lines.append('')
            lines.append(file.content.rstrip())
            if file.truncated:
                lines.append('')
                lines.append(f'_[truncated: `{file.relative}` exceeds the size limit]_')
            lines.append('')
        return '\n'.join(lines).rstrip() + '\n'

    def block(self, scopes: Iterable[Path]) -> InstructionBlock | None:
        """The replaceable workspace snapshot, or ``None`` if there are no files."""
        content = self.render(scopes)
        if not content:
            return None
        return InstructionBlock(name='workspace', content=content, on_change='rewrite', source='AGENTS.md/CLAUDE.md')

    def describe(self, scopes: Iterable[Path]) -> Sequence[str]:
        """Relative paths of the resolved files, for the UI."""
        return [file.relative for file in self.resolve(scopes)]
