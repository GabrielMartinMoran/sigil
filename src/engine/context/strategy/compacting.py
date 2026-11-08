"""View that collapses old turns into a deterministic summary."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from pydantic_ai.messages import CompactionPart, ModelMessage, ModelRequest

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.constants import (
    REASONING_TARGET_PERCENT,
    REASONING_TARGET_STEPS,
    SUMMARY_RECENT_TURNS,
    SUMMARY_TURN_CHARS,
)
from src.engine.context.compaction.coordinator import PolicyLookup
from src.engine.context.strategy.constants import (
    DEFAULT_IMAGE_MAX_PERCENT,
    DEFAULT_IMAGE_TURNS,
    PINNED_TOOLS,
)
from src.engine.context.strategy.rewriting import (
    count_images,
    fit_to_budget,
)
from src.engine.context.strategy.turn_summary import summarize_turn
from src.engine.context.strategy.turns import has_pinned_result, split_prompts
from src.engine.context.strategy.view_result import ViewResult
from src.engine.context.working_set import DEFAULT_WORKING_SET_LIMIT, render_working_set, touched_files


@dataclass(frozen=True, slots=True)
class CompactingStrategy:
    """View that collapses old turns into a deterministic summary.

    Recent turns stay intact: the model usually needs the exact details of the
    last turn on the next one. The old ones are replaced by a
    `CompactionPart` with the intent and the tool trail, without payloads.

    Turns that carry the result of a pinned tool (a loaded skill) are not
    compacted: their content is instruction, not data.

    The summary closes with the **working set**: the files the agent touched,
    derived from the full log. Without that, compacting erases the trail of what it was
    editing and the agent re-reads from scratch what it already had at hand.

    The log is not touched: compaction is a projection, just like the window.
    """

    keep_recent_prompts: int = SUMMARY_RECENT_TURNS
    reasoning_steps: int = REASONING_TARGET_STEPS
    """Operational reasoning target across turns."""
    reasoning_max_percent: int = REASONING_TARGET_PERCENT
    """Ceiling for the reasoning kept, as a percentage of the model's window."""
    image_turns: int = DEFAULT_IMAGE_TURNS
    """Rounds an image stays loaded, counting its own. Older ones leave their id behind."""
    image_max_percent: int = DEFAULT_IMAGE_MAX_PERCENT
    """Ceiling for the pixels that stay, as a percentage of the model's window."""
    max_summary_chars: int = SUMMARY_TURN_CHARS
    provider: str | None = None
    """The provider the request is going to: signed reasoning is only replayed to
    the provider that produced it."""
    pinned_tools: frozenset[str] = PINNED_TOOLS
    include_working_set: bool = True
    """Whether the summary closes with the touched files."""
    lookup: PolicyLookup | None = None
    """The level of detail each tool declared, by name. See `WindowedStrategy`."""
    working_set_limit: int = DEFAULT_WORKING_SET_LIMIT
    """How many files fit in the working set."""

    def view(self, messages: Sequence[ModelMessage], budget: ContextBudget) -> ViewResult:
        from src.engine.context.compaction.projection import finish, prepare
        from src.engine.context.strategy.windowed import WindowedStrategy

        policy = WindowedStrategy(
            reasoning_steps=self.reasoning_steps,
            reasoning_max_percent=self.reasoning_max_percent,
            pinned_tools=self.pinned_tools,
        ).policy()
        prepared = prepare(
            messages,
            budget,
            policy=policy,
            lookup=self.lookup,
            provider=self.provider,
            image_turns=self.image_turns,
            image_percent=self.image_max_percent,
        )
        prompts = split_prompts(prepared.messages)
        dropped_images = prepared.dropped_images
        recent_start = max(0, len(prompts) - self.keep_recent_prompts)
        old_prompts = prompts[:recent_start]
        recent_prompts = prompts[recent_start:]

        compacted = 0
        result: list[ModelMessage] = []
        pending: list[str] = []

        def flush() -> None:
            if pending:
                result.append(ModelRequest(parts=[CompactionPart(content='\n\n'.join(pending))]))
                pending.clear()

        for prompt in old_prompts:
            if has_pinned_result(prompt, self.pinned_tools):
                # Loaded instructions: kept whole and in place.
                flush()
                result.extend(prompt)
                continue
            text = summarize_turn(prompt, max_chars=self.max_summary_chars)
            if text:
                pending.append(text)
                compacted += 1
                # The summary keeps the intent, not the pixels: an image of a
                # compacted turn is gone, and saying so is part of the report.
                dropped_images += count_images(prompt)

        if compacted and self.include_working_set:
            # The working set comes from the full log, not from the old turns: what
            # the agent touched a step ago is also part of what it is
            # doing. It goes at the end of the summary so it sits as close as
            # possible to the turn in progress.
            working_set = render_working_set(touched_files(messages, limit=self.working_set_limit))
            if working_set:
                pending.append(working_set)
        flush()

        for prompt in recent_prompts:
            result.extend(prompt)
        prepared = replace(prepared, messages=result, compacted_prompts=compacted, dropped_images=dropped_images)
        return finish(
            messages,
            prepared,
            budget,
            policy=policy,
            lookup=self.lookup,
            provider=self.provider,
            keep_recent=self.keep_recent_prompts,
        )


def fit_keeping_summary(
    messages: list[ModelMessage],
    available: int,
    *,
    pinned_tools: frozenset[str] = frozenset(),
) -> tuple[list[ModelMessage], int]:
    """Like `fit_to_budget`, but it never discards the compaction summary."""
    head: list[ModelMessage] = []
    rest = list(messages)
    if rest and isinstance(rest[0], ModelRequest) and any(isinstance(part, CompactionPart) for part in rest[0].parts):
        head = [rest.pop(0)]
    return fit_to_budget(rest, available, pinned_tools=pinned_tools, keep_head=head)
