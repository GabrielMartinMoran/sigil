"""The ToolCompactor value of the tooling layer."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from src.core.tooling.encoded import encoded
from src.core.tooling.normalize import normalize

Compactor = Callable[[Any], Any]


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
