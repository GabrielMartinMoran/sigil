"""Debug view model: what the agent has in context and what it costs.

The debug view shows **the agent side** of the conversation. Two lists:

- **log**: the messages of the session, as they are persisted and seen in the chat.
- **view**: the messages the agent receives on the next request, after
  applying the context strategy (reasoning dropped, old tool results
  truncated, old turns collapsed or discarded).

The difference between the two is exactly what the UI does not show and what it
takes to understand why the agent forgot something. It also reports the fixed
cost of the request (instructions and tool schemas) and the prompt cache
usage, which is what tells whether the prompt prefix is stable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.engine.agents.turn_map import library_run_id
from src.engine.context.compaction.events import CompactionEvent
from src.engine.context.meter.cache import CacheSummary, RequestCacheRow, cache_summary, request_cache_rows
from src.engine.context.meter.request import RequestSnapshot
from src.engine.context.meter.snapshot import ContextSnapshot
from src.engine.context.strategy.view_result import ViewResult
from src.engine.context.tokens import estimate_message_tokens, estimate_tokens, part_text, part_tokens, tool_return_text

DEFAULT_PART_CHARS = 700
"""How many characters of each part are shown. The real cost goes separately, in tokens."""

DEFAULT_PART_LINES = 6
"""How many preview lines are shown. A tool result is 1% signal and 99% lines."""


@dataclass(frozen=True, slots=True)
class DebugPart:
    """One part of a message, with its cost and a bounded preview."""

    kind: str
    label: str
    tokens: int
    chars: int
    preview: str
    unknown_tokens: bool = False
    clipped_chars: int = 0
    """Characters not shown because of the preview clipping."""

    @property
    def clipped(self) -> bool:
        return self.clipped_chars > 0


@dataclass(frozen=True, slots=True)
class DebugMessage:
    """A message with its parts, measured in tokens."""

    index: int
    role: str
    """``request`` (what goes into the model) or ``response`` (what the model produced)."""
    tokens: int
    parts: tuple[DebugPart, ...] = ()
    instructions: tuple[str, ...] = ()
    """Names of the instruction blocks that travel with this request."""

    @property
    def kinds(self) -> str:
        """One-line summary: ``user · tool_return ×3``."""
        if not self.parts:
            return '(empty)'
        counts: dict[str, int] = {}
        for part in self.parts:
            counts[part.kind] = counts.get(part.kind, 0) + 1
        return ' · '.join(f'{kind} ×{count}' if count > 1 else kind for kind, count in counts.items())


@dataclass(frozen=True, slots=True)
class DebugTurn:
    index: int
    last_input: int | None
    input_total: int
    cache_read: int
    unmeasured: int
    measured: int
    cache_ratio: float | None
    events: tuple[CompactionEvent, ...] = ()
    """The compaction boundaries that fell inside this turn."""
    steps: tuple[RequestCacheRow | None, ...] = ()
    steps_before: int = 0
    step_events: tuple[tuple[CompactionEvent, ...], ...] = ()

    @property
    def context_used(self) -> int | None:
        """The prompt size of the turn's last request: what the turn cost in context."""
        return self.last_input


def _turn_groups(messages: list[ModelMessage]) -> list[list[ModelMessage]]:
    groups: list[list[ModelMessage]] = []
    current_run: str | None = None
    for message in messages:
        run = library_run_id(message)
        prompt = isinstance(message, ModelRequest) and any(isinstance(part, UserPromptPart) for part in message.parts)
        if not groups or (run and current_run and run != current_run) or (not run and not current_run and prompt):
            groups.append([])
        groups[-1].append(message)
        if run:
            current_run = run
    return groups


def _turn_steps(messages: list[ModelMessage], count: int) -> tuple[RequestCacheRow | None, ...]:
    rows = {row.index: row for row in request_cache_rows(messages)}
    return tuple(rows.get(index) for index in range(1, count + 1))


def _events_by_step(
    messages: list[ModelMessage], events: tuple[CompactionEvent, ...], steps_before: int
) -> tuple[tuple[CompactionEvent, ...], ...]:
    from src.engine.context.compaction.events import RECORD_KEY

    responses = [message for message in messages if isinstance(message, ModelResponse)]
    requests = {}
    for index, response in enumerate(responses):
        record = (response.metadata or {}).get(RECORD_KEY)
        if isinstance(record, dict) and record.get('status') != 'restored' and record.get('request_id'):
            requests[record['request_id']] = index
    result: list[list[CompactionEvent]] = [[] for _ in responses]
    for event in events:
        index = requests.get(event.request_id) if event.request_id else None
        if index is None:
            index = event.step - steps_before - 1
        if 0 <= index < len(result):
            result[index].append(event)
    return tuple(tuple(items) for items in result)


def debug_turns(
    messages: list[ModelMessage],
    events: Sequence[CompactionEvent] = (),
) -> tuple[DebugTurn, ...]:
    """One row per turn, with the compaction boundaries that happened inside it.

    An event sits at a step, and a step belongs to a turn: the boundary at step ``N``
    is attributed to the turn that contains step ``N``. The step counter runs over the
    whole execution, which is what makes the attribution stable across a resume.
    """
    from src.engine.context.compaction.events import RECORD_KEY

    owners = {}
    for message in messages:
        record = (message.metadata or {}).get(RECORD_KEY)
        if isinstance(record, dict) and record.get('request_id'):
            owner = record['request_id'].rsplit(':', 1)[0]
            for row in record.get('events', ()):
                owners[row['step'], row['reason']] = owner
    turns = []
    steps_before = 0
    for index, messages_in_turn in enumerate(_turn_groups(messages), start=1):
        steps_here = sum(1 for message in messages_in_turn if isinstance(message, ModelResponse))
        runs = {library_run_id(message) for message in messages_in_turn}
        in_turn = []
        for event in events:
            owner = (
                event.request_id.rsplit(':', 1)[0] if event.request_id else owners.get((event.step, str(event.reason)))
            )
            if (owner in runs) if owner else (steps_before < event.step <= steps_before + steps_here):
                in_turn.append(event)
        in_turn = tuple(in_turn)
        first_step = steps_before
        steps_before += steps_here
        responses = [message for message in messages_in_turn if isinstance(message, ModelResponse)]
        summary = cache_summary(messages_in_turn)
        last_usage = responses[-1].usage if responses else None
        last_input = (
            last_usage.input_tokens
            if last_usage is not None and last_usage.has_values() and not last_usage.unmeasured_requests
            else None
        )
        turns.append(
            DebugTurn(
                index,
                last_input,
                summary.input_tokens,
                summary.cache_read,
                summary.unmeasured,
                summary.requests,
                summary.hit_ratio,
                in_turn,
                _turn_steps(messages_in_turn, steps_here),
                first_step,
                _events_by_step(messages_in_turn, in_turn, first_step),
            )
        )
    return tuple(turns)


@dataclass(frozen=True, slots=True)
class DebugReport:
    """Everything the debug view paints."""

    snapshot: ContextSnapshot
    request: RequestSnapshot
    cache: CacheSummary
    turns: tuple[DebugTurn, ...] = ()
    log: tuple[DebugMessage, ...] = ()
    view: tuple[DebugMessage, ...] = ()
    view_result: ViewResult | None = None
    log_tokens: int = 0
    view_tokens: int = 0
    window: int | None = None
    effort: str | None = None
    model: str | None = None
    strategies_note: str = ''

    @property
    def dropped_tokens(self) -> int:
        """Log tokens the agent will not see on the next request."""
        return max(0, self.log_tokens - self.view_tokens)

    @property
    def reduction_percent(self) -> float | None:
        if not self.log_tokens:
            return None
        return self.dropped_tokens / self.log_tokens * 100.0

    @property
    def has_view(self) -> bool:
        return self.view_result is not None


def _clip(text: str, limit: int, max_lines: int) -> tuple[str, int]:
    """Clips by characters **and** by lines: a 700-char log is 100 lines of noise."""
    clipped = text[:limit]
    lines = clipped.splitlines()
    if len(lines) > max_lines:
        clipped = '\n'.join(lines[:max_lines])
    return clipped, len(text) - len(clipped)


def _part(kind: str, label: str, text: str, limit: int, max_lines: int) -> DebugPart:
    preview, clipped = _clip(text, limit, max_lines)
    return DebugPart(
        kind=kind,
        label=label,
        tokens=estimate_tokens(text),
        chars=len(text),
        preview=preview,
        clipped_chars=clipped,
    )


def _request_part(part: object, limit: int, max_lines: int) -> DebugPart | None:
    if isinstance(part, UserPromptPart):
        content = part_text(part)
        return replace(_part('user', 'user prompt', content, limit, max_lines), tokens=part_tokens(part))
    if isinstance(part, ToolReturnPart):
        text = tool_return_text(part)
        outcome = 'ok' if getattr(part, 'outcome', 'success') == 'success' else getattr(part, 'outcome', 'error')
        return replace(
            _part('tool_return', f'{part.tool_name} → {outcome}', text, limit, max_lines), tokens=part_tokens(part)
        )
    if isinstance(part, RetryPromptPart):
        return _part('retry', f'{part.tool_name or "tool"} retry', part_text(part), limit, max_lines)
    if isinstance(part, SystemPromptPart):
        return _part('system', 'system prompt', part.content or '', limit, max_lines)
    if isinstance(part, CompactionPart):
        return _part('compaction', 'compaction summary', part.content or '', limit, max_lines)
    return None


def _response_part(part: object, limit: int, max_lines: int) -> DebugPart | None:
    if isinstance(part, TextPart):
        return _part('text', 'assistant text', part.content or '', limit, max_lines)
    if isinstance(part, ThinkingPart):
        unknown = bool(part.signature) and not part.content
        return replace(
            _part(
                'thinking', 'thinking (size unknown)' if unknown else 'thinking', part.content or '', limit, max_lines
            ),
            unknown_tokens=unknown,
        )
    if isinstance(part, ToolCallPart):
        args = part.args_as_json_str() if hasattr(part, 'args_as_json_str') else str(part.args)
        return replace(_part('tool_call', f'call {part.tool_name}', args, limit, max_lines), tokens=part_tokens(part))
    return None


def _instructions_of(message: ModelMessage) -> tuple[str, ...]:
    """Names of the instruction blocks that travel in the request."""
    instructions = getattr(message, 'instructions', None)
    if not instructions:
        return ()
    if isinstance(instructions, str):
        return ('(inline)',)
    names = []
    for part in instructions:
        names.append(getattr(part, 'name', None) or '(base)')
    return tuple(names)


def debug_messages(
    messages: list[ModelMessage] | tuple[ModelMessage, ...],
    *,
    part_chars: int = DEFAULT_PART_CHARS,
    part_lines: int = DEFAULT_PART_LINES,
) -> list[DebugMessage]:
    """Turns pydantic-ai messages into measurable rows for the view."""
    rows: list[DebugMessage] = []
    for index, message in enumerate(messages):
        parts: list[DebugPart] = []
        if isinstance(message, ModelRequest):
            role = 'request'
            for part in message.parts:
                row = _request_part(part, part_chars, part_lines)
                if row is not None:
                    parts.append(row)
            instructions = _instructions_of(message)
        elif isinstance(message, ModelResponse):
            role = 'response'
            for part in message.parts:
                row = _response_part(part, part_chars, part_lines)
                if row is not None:
                    parts.append(row)
            instructions = ()
        else:
            role = 'other'
            instructions = ()
        rows.append(
            DebugMessage(
                index=index,
                role=role,
                tokens=estimate_message_tokens(message),
                parts=tuple(parts),
                instructions=instructions,
            )
        )
    return rows


def build_debug_report(
    messages: list[ModelMessage],
    *,
    snapshot: ContextSnapshot,
    request: RequestSnapshot,
    view_result: ViewResult | None = None,
    events: Sequence[CompactionEvent] | None = None,
    effort: str | None = None,
    model: str | None = None,
    part_chars: int = 0,
    part_lines: int = 0,
) -> DebugReport:
    """Builds the debug report from the log and the projected view."""
    log = debug_messages(messages, part_chars=part_chars, part_lines=part_lines)
    log_tokens = sum(row.tokens for row in log)

    if events is None and view_result is not None:
        events = (
            view_result.events[: view_result.plan.recorded_events]
            if view_result.plan is not None
            else view_result.events
        )
    if view_result is not None:
        view = debug_messages(view_result.messages, part_chars=part_chars, part_lines=part_lines)
        view_tokens = sum(row.tokens for row in view)
        strategies_note = (
            f'dropped reasoning: {view_result.dropped_thinking} · '
            f'compacted parts: {view_result.compacted_parts} · '
            f'dropped images: {view_result.dropped_images} · '
            f'awaiting summary: {view_result.summary_request is not None}'
        )
    else:
        view = []
        view_tokens = log_tokens
        strategies_note = ''

    return DebugReport(
        snapshot=snapshot,
        request=request,
        cache=cache_summary(messages),
        turns=debug_turns(messages, events or ()),
        log=tuple(log),
        view=tuple(view),
        view_result=view_result,
        log_tokens=log_tokens,
        view_tokens=view_tokens,
        window=snapshot.window,
        effort=effort,
        model=model,
        strategies_note=strategies_note,
    )
