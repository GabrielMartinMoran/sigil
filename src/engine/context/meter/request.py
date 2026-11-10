"""What travels on each request besides the history: instructions and tools.

`ModelRequestParameters` is the source of truth of what the provider serializes:
the already-resolved `instruction_parts` (with their name and whether they are dynamic) and the
tool definitions. Measuring from here, and not by introspecting the agent, avoids
the diagnostics lying when an instruction changes between requests.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic_ai.messages import ThinkingPart

from src.engine.context.tokens import estimate_tokens, part_tokens

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage


_PROMPT_KINDS = {
    'user-prompt': 'user',
    'tool-call': 'tool_call',
    'tool-return': 'tool_return',
    'builtin-tool-call': 'tool_call',
    'builtin-tool-return': 'tool_return',
    'retry-prompt': 'retry',
    'system-prompt': 'system',
}

_KNOWN_PROMPT_KINDS = frozenset(_PROMPT_KINDS.values()) | {'thinking', 'text', 'compaction'}


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
class PromptSection:
    """A sent history part's identity and cost, never its contents."""

    kind: str
    label: str
    tokens: int | None


@dataclass(frozen=True, slots=True)
class RequestSnapshot:
    """Fixed sections and content-free history costs of the last prepared request."""

    instructions: tuple[InstructionRow, ...] = ()
    tools: tuple[ToolRow, ...] = ()
    native_tools: int = 0
    """Native provider tools (they do not travel as a JSON schema)."""

    sections: tuple[PromptSection, ...] = ()
    history_captured: bool = False
    skills: dict[str, int] = field(default_factory=dict)
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
    if limit <= 0:
        return ''
    collapsed = ' '.join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + '…'


def snapshot_request(
    parameters: object, *, preview_chars: int = 120, messages: Sequence[ModelMessage] | None = None
) -> RequestSnapshot:
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
        if (getattr(part, 'content', '') or '').strip()
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

    return RequestSnapshot(
        instructions=instructions,
        tools=tools,
        native_tools=len(native),
        captured=True,
        skills=_skill_metrics(parts),
        sections=_prompt_sections(messages) if messages is not None else (),
        history_captured=messages is not None,
    )


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


def _skill_metrics(parts: Sequence[object]) -> dict[str, int]:
    """Estimate rendered skill costs and resource states without retaining their text."""
    text = '\n'.join(getattr(part, 'content', '') for part in parts if getattr(part, 'name', '') == 'loaded-skills')
    if not text:
        return {}
    resources = re.findall(r'<skill_resource\s+path="[^"]*"\s+status="([^"]+)">.*?</skill_resource>', text, re.DOTALL)
    resource_blocks = re.findall(
        r'<skill_resource\s+path="[^"]*"\s+status="[^"]+">.*?</skill_resource>', text, re.DOTALL
    )
    tokens = sum(estimate_tokens(block) for block in resource_blocks)
    return {
        'instructions_tokens': max(0, estimate_tokens(text) - tokens),
        'resources_tokens': tokens,
        'resources': len(resources),
        'partial': resources.count('partial'),
        'binary': resources.count('binary'),
        'unavailable': resources.count('unavailable'),
        'unavailable_skills': len(re.findall(r'<skill\s+name="[^"]*"\s+status="unavailable"\s*/>', text)),
    }


def _prompt_sections(messages: Sequence[ModelMessage]) -> tuple[PromptSection, ...]:
    from src.engine.agents.tree.prompt_tail import TAIL_MARKER

    return tuple(
        PromptSection('dynamic', 'Agent context', part_tokens(part))
        if (message.metadata or {}).get(TAIL_MARKER) and part_index == len(message.parts) - 1
        else _prompt_section(part, message_index + 1, part_index + 1)
        for message_index, message in enumerate(messages)
        for part_index, part in enumerate(message.parts)
    )


def _prompt_section(part: object, message_index: int, part_index: int) -> PromptSection:
    part_kind = getattr(part, 'part_kind', 'unknown')
    kind = _PROMPT_KINDS.get(part_kind, part_kind)
    opaque = part_kind.startswith('builtin-') or kind not in _KNOWN_PROMPT_KINDS
    unknown = opaque or (isinstance(part, ThinkingPart) and bool(part.signature) and not part.content)
    name = getattr(part, 'tool_name', None)
    label = f'{message_index}.{part_index} {part_kind}' + (f' {name}' if name else '')
    return PromptSection(kind, label, None if unknown else part_tokens(part))
