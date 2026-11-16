"""The load_file helper of the core layer."""

from __future__ import annotations

import json
from pathlib import Path

from textual.color import Color, ColorParseError

from src.core.theme_definition import DEFAULT_DARK, ThemeDefinition
from src.core.theme_error import ThemeError

SCHEMA_VERSION = 1


def _validated(data: object, *, source: Path | None) -> ThemeDefinition:
    """Reads a theme out of parsed JSON, or raises ``ThemeError``."""
    where = f' ({source})' if source is not None else ''
    if not isinstance(data, dict):
        raise ThemeError(f'A theme must be a JSON object{where}.')

    version = data.get('version', SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ThemeError(f'Unsupported theme version {version!r}{where}; expected {SCHEMA_VERSION}.')

    name = data.get('name')
    if not isinstance(name, str) or not name.strip():
        raise ThemeError(f'A theme needs a name{where}.')

    colors = data.get('colors')
    if not isinstance(colors, dict) or not colors:
        raise ThemeError(f'Theme {name!r} has no colors{where}.')
    if 'primary' not in colors:
        raise ThemeError(f'Theme {name!r} needs a primary color{where}.')

    parsed: dict[str, str] = {}
    for key, value in colors.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ThemeError(f'Theme {name!r} has a color that is not a string{where}.')
        try:
            Color.parse(value)
        except ColorParseError as exc:
            raise ThemeError(f'Theme {name!r}: {key}={value!r} is not a color{where}.') from exc
        parsed[key] = value

    dark = data.get('dark', DEFAULT_DARK)
    if not isinstance(dark, bool):
        raise ThemeError(f'Theme {name!r}: "dark" must be true or false{where}.')

    return ThemeDefinition(name=name.strip(), colors=parsed, dark=dark, source=source)


def load_file(path: Path) -> ThemeDefinition:
    """Reads one theme file. Raises ``ThemeError`` with the reason."""
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise ThemeError(f'Cannot read {path}: {exc}') from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ThemeError(f'{path} is not valid JSON: {exc}') from exc
    return _validated(data, source=path)
