"""Transport-independent human output, never the model's tool response.

Presenters accept normalized data, so live Pydantic objects, JSON tool results and
SQLite history all take the same path. Tools may register their own presenter;
clients never need a switch over tool names or a Pydantic repr fallback.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from src.engine.tooling.presentation import ToolSummary

MAX_OUTPUT_CHARS = 40_000
MAX_ITEMS = 100


@dataclass(frozen=True, slots=True)
class ToolOutput:
    text: str
    summary: ToolSummary | None = None


OutputPresenter = Callable[[Any], ToolOutput]
_PRESENTERS: dict[str, OutputPresenter] = {}


def register_output(name: str, presenter: OutputPresenter) -> None:
    """Declare a human output formatter independently of a client or agent instance."""
    _PRESENTERS[name] = presenter


def unregister_output(name: str) -> None:
    """Remove a presenter when its extension is unloaded."""
    _PRESENTERS.pop(name, None)


def _normalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode='json')
    if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
        try:
            return json.loads(value)
        except ValueError:
            return value
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value[:MAX_ITEMS]]
    return value


def present_output(name: str, result: Any) -> ToolOutput:
    """Format safely and bound only the human view; leave the original untouched."""
    from src.engine.tooling.output_defaults import builtin_presenters

    value = _normalize(result)
    presenter = _PRESENTERS.get(name) or builtin_presenters().get(name)
    try:
        shown = presenter(value) if presenter is not None else ToolOutput(readable(value))
    except Exception:  # noqa: BLE001 - presentation must never fail a tool or history replay
        shown = ToolOutput(readable(value))
    text = shown.text.rstrip()
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + '\n… (output truncated)'
    return ToolOutput(text, shown.summary)


def readable(value: Any, *, depth: int = 0) -> str:
    """Conservative fallback: labeled lines, not dict/list/BaseModel reprs."""
    value = _normalize(value)
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return f'Binary content ({len(value)} bytes)'
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    if isinstance(value, (int, float)):
        return str(value)
    if depth >= 6:
        return '… (nested output)'
    if isinstance(value, Mapping):
        if value.get('binary') or value.get('encoding') == 'base64':
            return 'Binary content; preview omitted.'
        if value.get('type') == 'text' and isinstance(value.get('text'), str):
            return readable(value['text'], depth=depth + 1)
        if value.get('type') in {'image', 'audio'}:
            return f'{value["type"].title()} content; preview omitted.'
        return _fields(value, depth)
    if isinstance(value, (list, tuple)):
        return '\n'.join(readable(item, depth=depth + 1) for item in value[:MAX_ITEMS])
    return 'Structured output (no human formatter declared)'


def _fields(value: Mapping[str, Any], depth: int) -> str:
    lines: list[str] = []
    for key, item in list(value.items())[:MAX_ITEMS]:
        if item is None:
            continue
        rendered = readable(item, depth=depth + 1)
        if '\n' in rendered or isinstance(item, (dict, list)):
            lines.append(f'{key}:\n' + '\n'.join(f'  {line}' for line in rendered.splitlines()))
        else:
            lines.append(f'{key}: {rendered}')
    return '\n'.join(lines)
