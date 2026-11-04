"""Parsing and validation of ``SKILL.md`` (YAML frontmatter + Markdown)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from src.engine.skills.models.skill_metadata import SkillMetadata
from src.engine.skills.models.skill_parse_error import SkillParseError

# The frontmatter must open on the first line with ``---`` and close with another
# ``---`` line. We support CRLF line endings and a leading BOM.
_FRONTMATTER_RE = re.compile(
    r'\A---[ \t]*\r?\n(?P<frontmatter>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)',
    re.DOTALL,
)

_NAME_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
_MAX_NAME_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 1024
_MAX_COMPATIBILITY_LENGTH = 500

# Keys the protocol recognizes. Unknown ones are kept in ``extra``
# so as not to reject skills from ecosystems that add their own fields.
_KNOWN_KEYS = {
    'name',
    'description',
    'license',
    'allowed-tools',
    'metadata',
    'compatibility',
    'disable-model-invocation',
}


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Splits the YAML frontmatter from the Markdown body.

    Returns ``(frontmatter, body)``. Raises ``SkillParseError`` if the
    document does not follow the protocol format.
    """
    text = text.removeprefix('\ufeff')

    if not text.startswith('---'):
        raise SkillParseError('SKILL.md must start with a YAML frontmatter block delimited by `---`.')

    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise SkillParseError('SKILL.md has an invalid or unterminated YAML frontmatter block.')

    raw = match.group('frontmatter')
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SkillParseError(f'Invalid YAML in SKILL.md frontmatter: {exc}') from exc

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise SkillParseError('SKILL.md frontmatter must be a YAML mapping.')

    return data, text[match.end() :]


def _clean_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise SkillParseError(f'`{field_name}` must be a string, got {type(value).__name__}.')
    return value.strip()


def _as_str_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part for part in value.split() if part)
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise SkillParseError(f'`{field_name}` entries must be strings.')
            items.append(item)
        return tuple(items)
    raise SkillParseError(f'`{field_name}` must be a string or a list of strings.')


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def build_metadata(
    data: dict[str, Any],
    *,
    skill_file: Path,
    source: str,
) -> SkillMetadata:
    """Validates the frontmatter and builds ``SkillMetadata``."""
    warnings: list[str] = []

    if 'name' not in data:
        raise SkillParseError('Missing required `name` in SKILL.md frontmatter.')
    if 'description' not in data:
        raise SkillParseError('Missing required `description` in SKILL.md frontmatter.')

    name = _clean_string(data.get('name'), 'name')
    if not name:
        raise SkillParseError('`name` cannot be empty.')
    if len(name) > _MAX_NAME_LENGTH:
        raise SkillParseError(f'`name` is too long ({len(name)} characters); maximum is {_MAX_NAME_LENGTH}.')
    if not _NAME_RE.match(name):
        raise SkillParseError(
            f"Invalid skill name '{name}': use kebab-case (lowercase letters, digits and single hyphens)."
        )

    description = _clean_string(data.get('description'), 'description')
    if not description:
        raise SkillParseError('`description` cannot be empty.')
    if len(description) > _MAX_DESCRIPTION_LENGTH:
        warnings.append(
            f'`description` has {len(description)} characters (recommended maximum is {_MAX_DESCRIPTION_LENGTH}).'
        )

    licence: str | None = None
    if data.get('license') is not None:
        licence = _clean_string(data.get('license'), 'license') or None

    compatibility: str | None = None
    if data.get('compatibility') is not None:
        compatibility = _clean_string(data.get('compatibility'), 'compatibility') or None
        if compatibility is not None and len(compatibility) > _MAX_COMPATIBILITY_LENGTH:
            warnings.append(
                f'`compatibility` has {len(compatibility)} characters '
                f'(recommended maximum is {_MAX_COMPATIBILITY_LENGTH}).'
            )

    allowed_tools = _as_str_list(data.get('allowed-tools'), 'allowed-tools')

    meta_value = data.get('metadata')
    meta: dict[str, Any] = {}
    if meta_value is not None:
        if not isinstance(meta_value, dict):
            raise SkillParseError('`metadata` must be a YAML mapping.')
        meta = dict(meta_value)

    model_invocable = True
    if 'disable-model-invocation' in data:
        model_invocable = not _as_bool(data.get('disable-model-invocation'))

    extra = {key: value for key, value in data.items() if key not in _KNOWN_KEYS}
    if extra:
        warnings.append('Unknown frontmatter keys kept in `extra`: ' + ', '.join(sorted(extra)) + '.')

    return SkillMetadata(
        name=name,
        description=description,
        path=skill_file.parent.resolve(),
        skill_file=skill_file.resolve(),
        source=source,
        license=licence,
        compatibility=compatibility,
        allowed_tools=allowed_tools,
        model_invocable=model_invocable,
        metadata=meta,
        extra=extra,
        warnings=tuple(warnings),
    )


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Returns only the parsed frontmatter of a ``SKILL.md``."""
    data, _ = split_frontmatter(text)
    return data


def parse_skill_metadata(skill_file: Path, *, source: str) -> SkillMetadata:
    """Reads and validates the ``SKILL.md`` of a skill."""
    skill_file = Path(skill_file)
    try:
        text = skill_file.read_text(encoding='utf-8')
    except OSError as exc:
        raise SkillParseError(f'Cannot read {skill_file}: {exc}') from exc
    data, _ = split_frontmatter(text)
    return build_metadata(data, skill_file=skill_file, source=source)
