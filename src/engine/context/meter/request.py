"""What travels on each request besides the history: instructions and tools.

`ModelRequestParameters` is the source of truth of what the provider serializes:
the already-resolved `instruction_parts` (with their name and whether they are dynamic) and the
tool definitions. Measuring from here, and not by introspecting the agent, avoids
the diagnostics lying when an instruction changes between requests.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.engine.context.tokens import estimate_tokens


@dataclass(frozen=True, slots=True)
class InstructionRow:
    """An instruction block as it was sent."""

    name: str
    dynamic: bool
    """Whether the content is recomputed on every request (and can therefore break the prefix)."""
    tokens: int
    preview: str


@dataclass(frozen=True, slots=True)
class ToolRow:
    """A tool schema as it was sent."""

    name: str
    tokens: int
    deferred: bool = False
    """Whether the schema was deferred (it does not travel until the history reveals it)."""


@dataclass(frozen=True, slots=True)
class RequestSnapshot:
    """Fixed cost of the last request: instructions and tool schemas."""

    instructions: tuple[InstructionRow, ...] = ()
    tools: tuple[ToolRow, ...] = ()
    native_tools: int = 0
    """Native provider tools (they do not travel as a JSON schema)."""

    captured: bool = False
    """Whether the snapshot comes from a real request. When false, the zeros are not a measurement.

    It happens in a freshly loaded session with `/load`: there is history, but no
    request ran in this process and therefore there are no observed instructions or
    tools.
    """

    @property
    def instructions_tokens(self) -> int:
        return sum(row.tokens for row in self.instructions)

    @property
    def tools_tokens(self) -> int:
        return sum(row.tokens for row in self.tools)

    @property
    def dynamic_instructions(self) -> tuple[InstructionRow, ...]:
        """Blocks recomputed per request: candidates to invalidate the cache."""
        return tuple(row for row in self.instructions if row.dynamic)


def _preview(text: str, limit: int = 120) -> str:
    collapsed = ' '.join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + '…'


def snapshot_request(parameters: object, *, preview_chars: int = 120) -> RequestSnapshot:
    """Builds the request snapshot from `ModelRequestParameters`.

    Without parameters there was no request: it returns an empty and **not captured**
    snapshot, so the diagnostics do not confuse "I did not measure" with "there are no instructions".
    """
    if parameters is None:
        return RequestSnapshot()

    parts = getattr(parameters, 'instruction_parts', None) or ()
    instructions = tuple(
        InstructionRow(
            name=getattr(part, 'name', None) or '(base)',
            dynamic=bool(getattr(part, 'dynamic', False)),
            tokens=estimate_tokens(getattr(part, 'content', '') or ''),
            preview=_preview(getattr(part, 'content', '') or '', preview_chars),
        )
        for part in parts
    )

    definitions = getattr(parameters, 'function_tools', None) or ()
    tools = tuple(
        ToolRow(
            name=getattr(tool, 'name', '') or '?',
            tokens=estimate_tokens(_tool_payload(tool)),
            deferred=bool(getattr(tool, 'defer_loading', False)),
        )
        for tool in definitions
    )

    native = getattr(parameters, 'native_tools', None) or ()

    return RequestSnapshot(instructions=instructions, tools=tools, native_tools=len(native), captured=True)


def _tool_payload(tool: object) -> str:
    """Approximate schema serialization, same as `overhead.measure_tool_definitions`."""
    import json

    payload = {
        'name': getattr(tool, 'name', ''),
        'description': getattr(tool, 'description', '') or '',
        'parameters': getattr(tool, 'parameters_json_schema', None) or {},
    }
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except TypeError, ValueError:
        return str(payload)
