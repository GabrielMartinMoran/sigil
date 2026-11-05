"""Tests for keeping the reasoning of the round in progress.

A round is everything between handing control back to the user: one run id, one
or more prompts if the user steered mid-round. While the agent is inside a round
it re-reads its own reasoning step after step; that is where a loop of
re-litigating a decision shows up. Past rounds are not working memory.
"""

from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ThinkingPart

from src.engine.context.budget import ContextBudget
from src.engine.context.strategy.compacting import CompactingStrategy
from src.engine.context.strategy.rewriting import keep_recent_steps, thinking_labels
from src.engine.context.strategy.turns import split_turns
from src.engine.context.strategy.windowed import WindowedStrategy
from src.engine.context.tokens import estimate_tokens

# --- helpers ---------------------------------------------------------------


def _request(text: str, *, run: str | None = 'r1') -> ModelRequest:
    return ModelRequest(parts=[TextPart(content=text)], run_id=run)


def _response(step: int, run: str | None = 'r1', *, thinking: ThinkingPart | None = None) -> ModelResponse:
    parts: list = [] if thinking is None else [thinking]
    parts.append(TextPart(content=f'step {step}'))
    return ModelResponse(parts=parts, run_id=run)


def _thinking(content: str, *, signature: str | None = None, provider: str | None = None) -> ThinkingPart:
    return ThinkingPart(content=content, signature=signature, provider_name=provider)


def _round(run: str, steps: int, *, signature_of: str | None = None, provider: str | None = None) -> list:
    """One round: a prompt and `steps` responses, each carrying reasoning."""
    messages: list = [_request(f'round {run}', run=run)]
    for step in range(steps):
        signature = f'sig-{run}-{step}' if signature_of else None
        messages.append(
            _response(step, run, thinking=_thinking(f'why {run}/{step}', signature=signature, provider=provider))
        )
    return messages


def _thinking_contents(messages: list) -> list[str]:
    return [
        part.content for message in messages for part in getattr(message, 'parts', ()) if isinstance(part, ThinkingPart)
    ]


def _runs_of_thinking(messages: list) -> list[str | None]:
    return [
        message.run_id
        for message in messages
        if isinstance(message, ModelResponse) and any(isinstance(part, ThinkingPart) for part in message.parts)
    ]


# --- current round ---------------------------------------------------------


def test_keeps_reasoning_of_the_round_in_progress():
    messages = [*_round('r1', 3), *_round('r2', 3)]

    result, kept, dropped = keep_recent_steps(messages, steps=5)

    assert [content for content in _thinking_contents(result) if content.startswith('why r2')] == [
        'why r2/0',
        'why r2/1',
        'why r2/2',
    ]
    assert kept == 3
    assert dropped == 3


def test_past_rounds_lose_their_reasoning():
    messages = [*_round('r1', 3), *_round('r2', 3)]

    result, _, _ = keep_recent_steps(messages, steps=5)

    assert all(not content.startswith('why r1') for content in _thinking_contents(result))


def test_keeps_only_the_last_steps_of_a_long_round():
    """The window is linear: a round with 200 steps does not resend 200 steps' worth.

    Resending the whole round costs a sum of sums —44M billed tokens on a measured
    317-step round— while the loop it fixes is local to a few steps.
    """
    messages = [*_round('r1', 200)]

    result, kept, dropped = keep_recent_steps(messages, steps=4)

    assert kept == 4
    assert dropped == 196
    assert _thinking_contents(result) == ['why r1/196', 'why r1/197', 'why r1/198', 'why r1/199']


def test_zero_steps_drops_everything():
    messages = [*_round('r1', 3)]

    result, kept, dropped = keep_recent_steps(messages, steps=0)

    assert kept == 0
    assert dropped == 3
    assert _thinking_contents(result) == []


def test_steps_needed_does_not_cross_a_round_boundary():
    """A short round keeps its own reasoning and does not borrow the previous one."""
    messages = [*_round('r1', 5), *_round('r2', 2)]

    result, kept, _ = keep_recent_steps(messages, steps=4)

    assert kept == 2
    assert _thinking_contents(result) == ['why r2/0', 'why r2/1']


def test_messages_without_run_id_are_their_own_round():
    """Old sessions predate `run_id`; grouping must not merge unrelated messages."""
    messages = [
        ModelRequest(parts=[TextPart(content='old prompt')]),
        ModelResponse(parts=[ThinkingPart(content='old reasoning')]),
        *_round('r2', 2),
    ]

    result, kept, _ = keep_recent_steps(messages, steps=2)

    assert kept == 2
    assert _thinking_contents(result) == ['why r2/0', 'why r2/1']


def test_turns_stay_whole():
    """Reasoning may go, but a turn never loses its structure."""
    messages = [*_round('r1', 2), *_round('r2', 2)]
    before = len(split_turns(messages))

    result, _, _ = keep_recent_steps(messages, steps=1)

    assert len(split_turns(result)) == before
    assert len(result) == len(messages)


# --- provider labels -------------------------------------------------------


def test_signed_reasoning_from_another_provider_is_still_dropped():
    """The budget only grows for what the receiving provider accepts back.

    Anthropic, Bedrock, Google and OpenAI sign the reasoning and only accept their
    own signatures. Keeping a signed block that belongs to a different provider
    spends tokens on something the adapter has to render as plain text anyway.
    """
    messages = [*_round('r1', 2, signature_of='r1', provider='anthropic')]

    result, kept, dropped = keep_recent_steps(messages, steps=5, provider='openai')

    assert kept == 0
    assert dropped == 2
    assert _thinking_contents(result) == []


def test_signed_reasoning_from_the_same_provider_is_kept():
    messages = [*_round_r1_provider()]

    result, kept, _ = keep_recent_steps(messages, steps=5, provider='anthropic')

    assert kept == 2
    assert len(_thinking_contents(result)) == 2


def _round_r1_provider() -> list:
    messages: list = [_request('round r1')]
    for step in range(2):
        messages.append(
            _response(step, thinking=_thinking(f'why {step}', signature=f'sig-{step}', provider='anthropic'))
        )
    return messages


def test_unsigned_reasoning_survives_a_provider_change():
    """Unsigned reasoning has no owner, so it can be replayed by anyone."""
    messages = [*_round('r1', 2)]

    _result, kept, _ = keep_recent_steps(messages, steps=5, provider='openai')

    assert kept == 2


def test_labels_find_the_round_by_skipping_other_runs():
    """`thinking_labels` reports which steps carry reasoning, per round."""
    messages = [*_round('r1', 2), *_round('r2', 3), *_round('r3', 1)]

    labels = thinking_labels(messages)

    assert labels == ['r1', 'r1', 'r2', 'r2', 'r2', 'r3']


# --- the strategies apply the policy ---------------------------------------


def _budget(tokens: int = 10_000_000) -> ContextBudget:
    return ContextBudget(window=tokens + 8_000, output_reserve=8_000)


def test_windowed_keeps_the_reasoning_of_the_round_in_progress():
    messages = [*_round('r1', 3), *_round('r2', 3)]

    result = WindowedStrategy(keep_thinking_steps=2).view(messages, _budget())

    kept = [content for content in _thinking_contents(result.messages) if content.startswith('why r2')]
    assert kept == ['why r2/1', 'why r2/2']
    assert result.dropped_thinking == 4


def test_windowed_with_zero_steps_behaves_like_before():
    messages = [*_round('r1', 3), *_round('r2', 3)]

    result = WindowedStrategy(keep_thinking_steps=0).view(messages, _budget())

    assert _thinking_contents(result.messages) == []
    assert result.dropped_thinking == 6


def test_windowed_all_keeps_everything():
    messages = [*_round('r1', 2), *_round('r2', 2)]

    result = WindowedStrategy(keep_thinking_steps=None).view(messages, _budget())

    assert len(_thinking_contents(result.messages)) == 4
    assert result.dropped_thinking == 0


def test_thinking_window_never_breaks_the_budget():
    """The kept reasoning is a lever like any other: if it does not fit, it goes.

    Reasoning is the cheapest thing to drop, so it is dropped before a tool result
    is truncated or a turn is discarded.
    """
    from src.engine.context.tokens import estimate_messages_tokens

    def round_with_long_reasoning(run: str, steps: int) -> list:
        messages: list = [_request(f'round {run}', run=run)]
        for step in range(steps):
            messages.append(_response(step, run, thinking=_thinking(f'why {run}/{step} ' + 'y' * 2_000)))
        return messages

    messages = [*round_with_long_reasoning('r1', 2), *round_with_long_reasoning('r2', 20)]

    # Room for the conversation plus a few steps of reasoning, not for all of it.
    without_reasoning = WindowedStrategy(keep_thinking_steps=0).view(messages, ContextBudget(window=None))
    room = estimate_messages_tokens(without_reasoning.messages)
    one_step = estimate_tokens('why r2/0 ' + 'y' * 2_000)
    allowance = one_step * 3

    tight = WindowedStrategy(keep_thinking_steps=20).view(
        messages, ContextBudget(window=room + allowance + 8_000, output_reserve=8_000)
    )

    kept = len(_thinking_contents(tight.messages))
    assert 0 < kept < 20
    assert tight.dropped_messages == 0
    assert tight.estimated_tokens <= room + allowance


def test_compacting_applies_the_same_policy():
    messages = [*_round('r1', 3), *_round('r2', 3)]

    result = CompactingStrategy(keep_thinking_steps=1).view(messages, _budget())

    kept = _thinking_contents(result.messages)
    assert kept == ['why r2/2']


def test_compacting_all_keeps_everything():
    messages = [*_round('r1', 2), *_round('r2', 2)]

    result = CompactingStrategy(keep_thinking_steps=None).view(messages, _budget())

    assert len(_thinking_contents(result.messages)) == 4
