"""Parsing and validation of an agent profile (Markdown + frontmatter).

An agent profile is a Markdown file with three known keys in its frontmatter:

```markdown
---
name: wizard
description: What this agent is for, in one line.
permissions:
  allow: ['read_*', 'write_file', 'patch_file']
  deny: [run]
---
The prompt. It is appended after Sigil's base prompt and sets the behaviour.
```

The body is the whole point of the file: it is injected as an instruction block,
so a profile changes how Sigil works without touching the base prompt.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.engine.agents.profile.models.agent_parse_error import AgentParseError
from src.engine.agents.profile.models.agent_profile import AgentProfile
from src.engine.agents.profile.permissions import parse_permissions
from src.engine.markdown.frontmatter import FrontmatterError
from src.engine.markdown.frontmatter import split_frontmatter as _split

_NAME_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
_MAX_NAME_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 1024

_KNOWN_KEYS = {'name', 'description', 'permissions'}


def _clean_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise AgentParseError(f'`{field_name}` must be a string, got {type(value).__name__}.')
    return value.strip()


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Splits the frontmatter from the body, as ``AgentParseError`` on failure."""
    try:
        return _split(text, label='An agent profile')
    except FrontmatterError as exc:
        raise AgentParseError(str(exc)) from exc


def build_profile(data: dict[str, Any], *, profile_file: Path, source: str) -> AgentProfile:
    """Validates the frontmatter and builds the ``AgentProfile``."""
    if 'name' not in data:
        raise AgentParseError('Missing required `name` in the frontmatter.')
    if 'description' not in data:
        raise AgentParseError('Missing required `description` in the frontmatter.')

    name = _clean_string(data.get('name'), 'name')
    if not name:
        raise AgentParseError('`name` cannot be empty.')
    if len(name) > _MAX_NAME_LENGTH:
        raise AgentParseError(f'`name` is too long ({len(name)} characters); maximum is {_MAX_NAME_LENGTH}.')
    if not _NAME_RE.match(name):
        raise AgentParseError(
            f"Invalid agent name '{name}': use kebab-case (lowercase letters, digits and single hyphens)."
        )

    description = _clean_string(data.get('description'), 'description')
    if not description:
        raise AgentParseError('`description` cannot be empty.')
    if len(description) > _MAX_DESCRIPTION_LENGTH:
        raise AgentParseError(
            f'`description` is too long ({len(description)} characters); maximum is {_MAX_DESCRIPTION_LENGTH}.'
        )

    unknown = set(data) - _KNOWN_KEYS
    if unknown:
        names = ', '.join(sorted(str(key) for key in unknown))
        raise AgentParseError(f'Unknown frontmatter keys: {names}. Use `name`, `description` and `permissions`.')

    return AgentProfile(
        name=name,
        description=description,
        path=profile_file.parent.resolve(),
        profile_file=profile_file.resolve(),
        source=source,
        permissions=parse_permissions(data.get('permissions')),
    )


def parse_profile(profile_file: Path, *, source: str) -> tuple[AgentProfile, str]:
    """Reads a profile and returns ``(metadata, body)``."""
    profile_file = Path(profile_file)
    try:
        text = profile_file.read_text(encoding='utf-8')
    except OSError as exc:
        raise AgentParseError(f'Cannot read {profile_file}: {exc}') from exc
    data, body = split_frontmatter(text)
    return build_profile(data, profile_file=profile_file, source=source), body.strip()
