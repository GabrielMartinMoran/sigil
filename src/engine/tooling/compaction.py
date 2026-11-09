"""The single compacted representation a tool declares for its own calls.

A tool result and its arguments stay in the transcript forever: every later request
resends them. Most of that weight is detail nobody reads again — the body of a file,
the tail of a test run, the JSON envelope around three identifiers. A compactor is the
honest way to say *what* a call still means when the exact bytes stop mattering.

Every Sigil tool declares both functions next to its registration, like its
permission: `input` reduces the arguments, `output` reduces the result. Tools without a
declaration (MCP, external ones) get `GENERIC_COMPACTOR`, which is a size policy, not a
semantic one.

There is exactly one compacted representation, not a ladder: a call is either as it was
or reduced once. That keeps the projection a pure function of the transcript and makes
the persisted decision a set of call identities instead of a frontier per level.

The rules a declaration must keep:

- It is a function of the value, nothing else: no IO, no clock, no model call. The view
  has to be reproducible from the transcript.
- It always starts from the original value, so reducing is not a chain of losses.
- It never invents information the original did not have.
- Identity, status and errors survive. A failed call that reads like a successful one is
  worse than no compaction at all.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

GENERIC_LIMIT_CHARS = 500
"""What an external tool keeps when nobody can say what matters in its payload."""

OMITTED = '\n\n[{label}truncated: {omitted:,} characters omitted]'
"""Marks a value that was shortened, so the model knows it can ask again.

The label says *what* was shortened —a tool result, an argument— because "some text is
missing" is not enough to decide whether the call has to be made again.
"""

Compactor = Callable[[Any], Any]
"""``value -> compacted value``. Pure and total."""


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


def compact_text(value: Any, limit: int = GENERIC_LIMIT_CHARS) -> Any:
    """Bound the whole serialized value, not each individual leaf.

    Structured payloads become an explicit preview envelope when necessary. Historical
    arguments remain valid JSON objects; they are never executed or schema-validated.
    Opaque binary values and indispensable status/error fields may exceed the target;
    the coordinator reports those exceptions instead of corrupting them.
    """
    if callable(getattr(value, 'model_dump', None)):
        value = value.model_dump()
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


def compact_json(value: Any, limit: int = GENERIC_LIMIT_CHARS) -> str:
    """Parseable JSON whose complete serialized size respects the generic target."""
    reduced = compact_text(value, limit)
    text = encoded(reduced)
    if isinstance(reduced, str) and len(text) > limit:
        return encoded(compact_text({'text': reduced}, limit))
    return text


@dataclass(frozen=True, slots=True)
class ToolCompactor:
    """What a tool keeps of its arguments and of its result when it is compacted."""

    input: Compactor
    """Reduces the arguments of the call."""

    output: Compactor
    """Reduces the result of the call."""

    preserve: frozenset[str] = frozenset()
    """Argument names that are never shortened: paths, ids, and anything an error names."""

    def __post_init__(self) -> None:
        if not callable(self.input) or not callable(self.output):
            raise TypeError('A tool compactor requires callable input and output formatters')

    def compact_input(self, args: Any) -> Any:
        return self._bounded(args, self.input, preserve=self.preserve)

    def compact_output(self, value: Any) -> Any:
        return self._bounded(value, self.output)

    @staticmethod
    def _bounded(value: Any, compact: Compactor, *, preserve: frozenset[str] = frozenset()) -> Any:
        """Never expand a value: the original wins when the compactor does not shrink it."""
        normalized = normalize(value)
        candidate = compact(normalized)
        if isinstance(candidate, Mapping) and isinstance(normalized, Mapping):
            merged: dict[str, Any] = {**candidate, **{key: normalized[key] for key in preserve if key in normalized}}
            if 'truncated' not in merged and 'omitted' not in merged:
                omitted = sorted((set(normalized) - set(merged)) | set(merged.get('omitted_fields', [])))
                if omitted:
                    merged['omitted_fields'] = omitted
            candidate = merged
        return candidate if len(encoded(candidate)) < len(encoded(normalized)) else normalized


GENERIC_COMPACTOR = ToolCompactor(input=compact_text, output=compact_text)
"""Policy of a tool that did not declare one: sizes only, no semantics.

A tool from outside this project has nobody to decide what it keeps, so the size is the
only honest thing to reduce.
"""


__all__ = [
    'GENERIC_COMPACTOR',
    'GENERIC_LIMIT_CHARS',
    'Compactor',
    'ToolCompactor',
    'compact_json',
    'compact_text',
    'contains_binary',
    'encoded',
    'normalize',
    'shorten',
]
