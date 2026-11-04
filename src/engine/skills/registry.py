"""Index of discovered skills and load/read operations.

The registry is the only piece that knows the roots and the index state. The
tools and the instruction blocks ask it for metadata, bodies and resources; they do not
know where they came from.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from src.engine.skills.discovery import (
    DEFAULT_MAX_READ_BYTES,
    MAX_READ_BYTES,
    default_roots,
    list_resources,
    safe_join,
)
from src.engine.skills.models.skill import Skill
from src.engine.skills.models.skill_metadata import SkillMetadata
from src.engine.skills.models.skill_not_found import SkillNotFound
from src.engine.skills.models.skill_parse_error import SkillParseError
from src.engine.skills.models.skill_resource import SkillResource
from src.engine.skills.models.skill_resource_error import SkillResourceError
from src.engine.skills.parser import parse_skill_metadata, split_frontmatter


class SkillRegistry:
    """Index of discovered skills and load/read operations."""

    def __init__(self, roots: Sequence[tuple[str, Path]] | None = None) -> None:
        self._roots: list[tuple[str, Path]] = [
            (source, Path(path).expanduser()) for source, path in (roots if roots is not None else default_roots())
        ]
        self._skills: dict[str, SkillMetadata] = {}
        self._aliases: dict[str, str] = {}
        self.errors: list[str] = []
        self._discover()

    # --- discovery ---------------------------------------------------------

    def _discover(self) -> None:
        for source, root in self._roots:
            if not root.is_dir():
                continue
            try:
                entries = sorted(root.iterdir(), key=lambda path: path.name)
            except OSError as exc:
                self.errors.append(f'Cannot list skills directory {root}: {exc}')
                continue

            for entry in entries:
                skill_file = entry / 'SKILL.md'
                if not entry.is_dir() or not skill_file.is_file():
                    continue
                try:
                    metadata = parse_skill_metadata(skill_file, source=source)
                except SkillParseError as exc:
                    self.errors.append(f'{entry}: {exc}')
                    continue

                if metadata.name in self._skills:
                    # There is already a skill with that name in a higher-precedence
                    # root; the current one stays shadowed.
                    continue

                self._skills[metadata.name] = metadata
                self._aliases.setdefault(metadata.name, metadata.name)
                # Allow lookup by directory name even if the frontmatter
                # uses another identifier.
                self._aliases.setdefault(entry.name, metadata.name)

    def refresh(self) -> SkillRegistry:
        """Rescans the roots (in case a skill was installed)."""
        self._skills.clear()
        self._aliases.clear()
        self.errors.clear()
        self._discover()
        return self

    # --- queries -----------------------------------------------------------

    @property
    def roots(self) -> tuple[tuple[str, Path], ...]:
        return tuple(self._roots)

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None

    def list(self) -> list[SkillMetadata]:
        """All skills, sorted by name."""
        return sorted(self._skills.values(), key=lambda skill: skill.name)

    def get(self, name: str) -> SkillMetadata | None:
        """Looks for a skill by canonical name or by directory name."""
        if not isinstance(name, str):
            return None
        key = name.strip()
        if not key:
            return None
        return self._skills.get(self._aliases.get(key, key))

    def require(self, name: str) -> SkillMetadata:
        metadata = self.get(name)
        if metadata is None:
            available = ', '.join(skill.name for skill in self.list()) or 'none'
            raise SkillNotFound(f"Unknown skill '{name}'. Available skills: {available}.")
        return metadata

    # --- level 2: SKILL.md body --------------------------------------------

    def load(self, name: str) -> Skill:
        """Loads the ``SKILL.md`` body and lists the skill resources."""
        metadata = self.require(name)
        try:
            text = metadata.skill_file.read_text(encoding='utf-8')
        except OSError as exc:
            raise SkillParseError(f'Cannot read {metadata.skill_file}: {exc}') from exc
        _, body = split_frontmatter(text)
        return Skill(metadata=metadata, body=body.strip(), resources=tuple(self.list_resources(metadata)))

    # --- level 3: resources --------------------------------------------------

    def list_resources(self, metadata: SkillMetadata) -> list[SkillResource]:
        return list_resources(metadata)

    def resolve_resource(self, name: str, relative_path: str) -> Path:
        """Resolves a resource guaranteeing it stays inside the skill."""
        return safe_join(self.require(name), relative_path)

    def read_resource(
        self,
        name: str,
        relative_path: str,
        *,
        max_bytes: int = DEFAULT_MAX_READ_BYTES,
    ) -> tuple[bytes, bool, int]:
        """Reads a resource and returns ``(bytes, truncated, total_size)``."""
        target = self.resolve_resource(name, relative_path)
        if not target.is_file():
            raise SkillResourceError(f'Resource not found: {relative_path!r}.')

        limit = max(1, min(max_bytes, MAX_READ_BYTES))
        try:
            total_size = target.stat().st_size
            with target.open('rb') as handle:
                data = handle.read(limit + 1)
        except OSError as exc:
            raise SkillResourceError(f'Cannot read resource {relative_path!r}: {exc}') from exc

        return data[:limit], len(data) > limit, total_size

    # --- diagnostics --------------------------------------------------------

    def iter_errors(self) -> Iterable[str]:
        return tuple(self.errors)
