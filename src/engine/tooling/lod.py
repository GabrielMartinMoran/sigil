"""Levels of detail a tool declares for its own input and output.

A tool result and its arguments stay in the transcript forever: every later request
resends them. Most of that weight is detail nobody reads again — the body of a file,
the tail of a test run, the JSON envelope around three identifiers. A level of detail
is the honest way to say *what* a call still means when the exact bytes stop mattering.

Every Sigil tool declares both functions next to the registration, like its
permission: `input` reduces the arguments, `output` reduces the result. Tools without a
declaration (MCP, external ones) get `GENERIC_LOD`, which is a size policy, not a
semantic one.

The rules a declaration must keep:

- It is a function of the value and the level, nothing else: no IO, no clock, no
  model call. The view has to be reproducible from the transcript.
- It always starts from the original value, so a level is not a chain of losses.
- A more detailed level never invents information the original did not have.
- Identity, status and errors survive at every level. A failed call that reads like a
  successful one is worse than no compaction at all.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from src.engine.tooling.lod_constants import GENERIC_LOD_CHARS


class ToolLOD(IntEnum):
    """How much of a call survives in the model view. Higher is smaller."""

    FULL = 0
    """The original value, untouched."""

    LOD_1 = 1
    """Mild: long strings shortened, structure and identifiers intact."""

    LOD_2 = 2
    """Rough: the meaning of the call, without its payload."""

    LOD_3 = 3
    """Stub: what the call was and whether it worked."""


LOD_LEVELS: tuple[ToolLOD, ...] = (ToolLOD.FULL, ToolLOD.LOD_1, ToolLOD.LOD_2, ToolLOD.LOD_3)

LOD_TARGET_CHARS: dict[ToolLOD, int] = {
    ToolLOD.FULL: 0,
    ToolLOD.LOD_1: GENERIC_LOD_CHARS[0],
    ToolLOD.LOD_2: GENERIC_LOD_CHARS[1],
    ToolLOD.LOD_3: GENERIC_LOD_CHARS[2],
}
"""Size each level aims for when the tool has no semantic opinion.

The complete value is bounded unless indispensable status/error fields or opaque
binary content prevent safe reduction. Such exceptions are reported by the coordinator.
"""

OMITTED = '\n\n[{label}truncated: {omitted:,} characters omitted]'
"""Marks a value that was shortened, so the model knows it can ask again.

The label says *what* was shortened —a tool result, an argument— because "some text is
missing" is not enough to decide whether the call has to be made again.
"""

Compact = Callable[[Any, ToolLOD], Any]
"""``(value, level) -> compacted value``. Pure and total."""


def shorten(text: str, limit: int, *, label: str = '') -> str:
    """``text`` cut to ``limit`` characters, with a notice when something was lost."""
    if limit <= 0 or len(text) <= limit:
        return text
    prefix = f'{label} ' if label else ''
    notice = OMITTED.format(label=prefix, omitted=len(text))
    if len(notice) >= limit:
        return '[truncated]'[:limit]
    head = limit - len(notice)
    # Include the notice itself in the budget and report the actual discarded bytes.
    for _ in range(4):
        notice = OMITTED.format(label=prefix, omitted=len(text) - head)
        head = max(0, limit - len(notice))
    return text[:head] + notice


def limit_for(level: ToolLOD) -> int:
    """The size target of a level, or ``0`` for ``FULL``."""
    return LOD_TARGET_CHARS[level]


def normalize(value: Any) -> Any:
    """Live models and their stored JSON form use exactly the same policy path."""
    if callable(getattr(value, 'model_dump', None)):
        return normalize(value.model_dump())
    if isinstance(value, Mapping):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    return value


def encoded(value: Any) -> str:
    """The JSON size used by the generic policy, including escaping and separators."""
    return json.dumps(normalize(value), ensure_ascii=False, default=str)


def contains_binary(value: Any) -> bool:
    """Opaque or multimodal payloads must not be stringified into partial bytes."""
    from pydantic_ai.messages import BinaryContent, FileUrl

    if isinstance(value, (bytes, BinaryContent, FileUrl)):
        return True
    if callable(getattr(value, 'model_dump', None)):
        return contains_binary(value.model_dump())
    if isinstance(value, Mapping):
        return bool(
            value.get('binary')
            or value.get('encoding') == 'base64'
            or value.get('format') == 'content'
            or value.get('type') in ('image', 'audio')
            or 'blob' in value
        ) or any(contains_binary(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_binary(item) for item in value)
    return False


_PROTECTED_FIELDS = frozenset({'id', 'status', 'error', 'errors', 'isError', 'outcome', 'returncode', 'exit_code'})


def _nested_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        kept = {key: item for key, item in value.items() if key in _PROTECTED_FIELDS}
        for key, item in value.items():
            if key not in kept and isinstance(item, (Mapping, list, tuple)):
                nested = _nested_metadata(item)
                if nested:
                    kept[key] = nested
        return kept
    if isinstance(value, (list, tuple)):
        return {str(index): metadata for index, item in enumerate(value) if (metadata := _nested_metadata(item))}
    return {}


def compact_text(value: Any, level: ToolLOD) -> Any:
    """Bound the whole serialized value, not each individual leaf.

    Structured payloads become an explicit preview envelope when necessary. Historical
    arguments remain valid JSON objects; they are never executed or schema-validated.
    Opaque binary values and indispensable status/error fields may exceed the target;
    the coordinator reports those exceptions instead of corrupting them.
    """
    if level is ToolLOD.FULL:
        return value
    if callable(getattr(value, 'model_dump', None)):
        value = value.model_dump()
    limit = limit_for(level)
    if contains_binary(value):
        return value
    if isinstance(value, str):
        return shorten(value, limit)
    text = encoded(value)
    if len(text) <= limit:
        return value
    envelope: dict[str, Any] = {'truncated': True}
    if isinstance(value, Mapping):
        for key in sorted(_PROTECTED_FIELDS):
            if key in value:
                envelope[key] = value[key]
        nested = _nested_metadata({key: item for key, item in value.items() if key not in _PROTECTED_FIELDS})
        if nested:
            envelope['metadata'] = nested
    if len(encoded(envelope)) >= limit:
        return envelope
    low, high = 0, min(len(text), limit)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = {**envelope, 'preview': text[:middle]}
        if len(encoded(candidate)) <= limit:
            low = middle
        else:
            high = middle - 1
    if low:
        envelope['preview'] = text[:low]
    return envelope


def compact_json(value: Any, level: ToolLOD) -> str:
    """Parseable JSON whose complete serialized size respects the generic target."""
    reduced = compact_text(value, level)
    text = encoded(reduced)
    if level is not ToolLOD.FULL and isinstance(reduced, str) and len(text) > limit_for(level):
        return encoded(compact_text({'text': reduced}, level))
    return text


GENERIC_LOD_INPUT: Compact = compact_text
"""Input policy of a tool that declares none: shorten its string arguments."""

GENERIC_LOD_OUTPUT: Compact = compact_text
"""Output policy of a tool that declares none, kept for symmetry."""


@dataclass(frozen=True, slots=True)
class ToolLODPolicy:
    """What a tool keeps of its arguments and of its result, level by level."""

    input: Compact
    """Reduces the arguments of the call."""

    output: Compact
    """Reduces the result of the call."""

    preserve: frozenset[str] = frozenset()
    """Argument names that are never shortened: paths, ids, and anything an error names."""

    def __post_init__(self) -> None:
        if not callable(self.input) or not callable(self.output):
            raise TypeError('A LOD policy requires callable input and output formatters')

    def compact_input(self, args: Any, level: ToolLOD) -> Any:
        if level is ToolLOD.FULL:
            return args
        return self._smallest(args, level, self.input, preserve=self.preserve)

    def compact_output(self, value: Any, level: ToolLOD) -> Any:
        return value if level is ToolLOD.FULL else self._smallest(value, level, self.output)

    @staticmethod
    def _smallest(value: Any, level: ToolLOD, compact: Compact, *, preserve: frozenset[str] = frozenset()) -> Any:
        """Never expand a short result, and never regain detail at a coarser level."""
        normalized = normalize(value)
        best = normalized
        size = len(encoded(normalized))
        for candidate_level in (ToolLOD(number) for number in range(1, int(level) + 1)):
            candidate = compact(normalized, candidate_level)
            if isinstance(candidate, Mapping) and isinstance(normalized, Mapping):
                candidate = {**candidate, **{key: normalized[key] for key in preserve if key in normalized}}
                if 'truncated' not in candidate and 'omitted' not in candidate:
                    omitted = sorted((set(normalized) - set(candidate)) | set(candidate.get('omitted_fields', [])))
                    if omitted:
                        candidate['omitted_fields'] = omitted
            candidate_size = len(encoded(candidate))
            if candidate_size < size:
                best, size = candidate, candidate_size
        return best


GENERIC_LOD = ToolLODPolicy(input=GENERIC_LOD_INPUT, output=GENERIC_LOD_OUTPUT)
"""Policy of a tool that did not declare one: sizes only, no semantics.

Its ladder is fixed —2.000, 500 and 100 characters— and it is not derived from any
setting: a tool from outside this project has nobody to decide what it keeps, so the
size is the only honest thing to reduce.
"""


__all__ = [
    'GENERIC_LOD',
    'GENERIC_LOD_INPUT',
    'GENERIC_LOD_OUTPUT',
    'LOD_LEVELS',
    'LOD_TARGET_CHARS',
    'Compact',
    'ToolLOD',
    'ToolLODPolicy',
    'compact_json',
    'compact_text',
    'limit_for',
    'shorten',
]
