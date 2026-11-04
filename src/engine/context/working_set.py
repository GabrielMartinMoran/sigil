"""Working set: the files the agent touched, derived from the log.

When compaction collapses old turns into a summary, the payloads are
lost (which is what you want), but with them goes the trail of *which
files* the agent was touching. That trail is cheap and is what prevents the
agent from re-reading from scratch, after compacting, what it already had at hand.

The working set is **derived from the log**, it is not kept in a separate state:

- it is deterministic, just like the view;
- it cannot get out of sync with the history;
- it survives `/load` without persisting anything extra.

Only tools that declare a workspace path count (`PATH_TOOLS`). `run`
stays out: it receives shell commands, not paths, and guessing which files it touches
would be making information up.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart

from src.engine.context.scope import PATH_TOOLS

DEFAULT_WORKING_SET_LIMIT = 20
"""Files kept in the summary. More than that stops being a summary."""

READ_TOOLS: frozenset[str] = frozenset({'read_file'})
"""Read-only tools. The rest of `PATH_TOOLS` write."""


@dataclass(frozen=True, slots=True)
class TouchedFile:
    """A file touched by the agent, with the trail of what was done to it."""

    path: str
    tools: tuple[str, ...]
    """Tools that touched it, in order of first appearance, without repeats."""

    @property
    def written(self) -> bool:
        return any(tool not in READ_TOOLS for tool in self.tools)

    def render(self) -> str:
        return f'- `{self.path}` ({", ".join(self.tools)})'


@dataclass(slots=True)
class _Entry:
    path: str
    tools: list[str] = field(default_factory=list)
    order: int = 0
    """Position of the last time it was touched. Higher is more recent."""


def _normalize(raw: str) -> str:
    """Normalizes a path to deduplicate without inventing absolute paths."""
    path = raw.strip().replace('\\', '/')
    while path.startswith('./'):
        path = path[2:]
    return path


def _args_mapping(args: Any) -> Mapping[str, Any] | None:
    """Args of a `ToolCallPart` as a mapping.

    pydantic-ai leaves `args` as a dict when it could parse it and as a JSON string
    when the model emitted something that did not validate. A string that does not parse
    contributes no paths: it is ignored instead of breaking the view.
    """
    if isinstance(args, Mapping):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except ValueError:
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def touched_files(
    messages: Sequence[ModelMessage],
    *,
    limit: int = DEFAULT_WORKING_SET_LIMIT,
) -> list[TouchedFile]:
    """Files touched by the tools, most recent first.

    The order is by last interaction: what the agent just touched is what
    it most likely needs on the next step. The tiebreaker is the
    order of first appearance, so two files touched in the same
    message stay stable.
    """
    entries: dict[str, _Entry] = {}
    for position, message in enumerate(messages):
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if not isinstance(part, ToolCallPart):
                continue
            arg_names = PATH_TOOLS.get(part.tool_name)
            if not arg_names:
                continue
            args = _args_mapping(part.args)
            if args is None:
                continue
            for arg_name in arg_names:
                raw = args.get(arg_name)
                if not isinstance(raw, str) or not raw.strip():
                    continue
                path = _normalize(raw)
                entry = entries.get(path)
                if entry is None:
                    entry = _Entry(path=path, order=position)
                    entries[path] = entry
                entry.order = position
                if part.tool_name not in entry.tools:
                    entry.tools.append(part.tool_name)

    ordered = sorted(entries.values(), key=lambda entry: (-entry.order, entry.path))
    return [TouchedFile(path=entry.path, tools=tuple(entry.tools)) for entry in ordered[:limit]]


def render_working_set(files: Sequence[TouchedFile]) -> str:
    """Working set text for the compaction summary."""
    if not files:
        return ''
    lines = ['Files touched so far (most recent first):']
    lines.extend(file.render() for file in files)
    return '\n'.join(lines)
