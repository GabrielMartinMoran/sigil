"""Shared agents test support."""

from __future__ import annotations

from pathlib import Path

FRONTMATTER = '---\nname: {name}\ndescription: {description}\n{extra}---\n'


def write_profile(
    root: Path,
    name: str,
    *,
    description: str | None = None,
    body: str = 'Be nice.',
    extra: str = '',
    filename: str | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / (filename or f'{name}.md')
    path.write_text(
        FRONTMATTER.format(name=name, description=description or f'Profile {name}.', extra=extra) + body,
        encoding='utf-8',
    )
    return path


def _builtin_root() -> Path:
    from src.core.agents.profile.discovery import BUILTIN_DIRNAME

    return BUILTIN_DIRNAME


def _TestModel():
    from pydantic_ai.models.test import TestModel

    return TestModel()
