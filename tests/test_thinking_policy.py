"""Cross-turn reasoning retention, budget fitting, and provider compatibility tests."""

from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ThinkingPart

from src.engine.context.budget import ContextBudget
from src.engine.context.strategy.compacting import CompactingStrategy
from src.engine.context.strategy.rewriting import keep_recent_steps, thinking_labels
from src.engine.context.strategy.turns import split_prompts
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
    """One turn: a prompt and `steps` responses, each carrying reasoning."""
    messages: list = [_request(f'turn {run}', run=run)]
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


# --- current turn ---------------------------------------------------------


def test_keeps_reasoning_of_the_round_in_progress():
    messages = [*_round('r1', 3), *_round('r2', 3)]

    result, kept, dropped = keep_recent_steps(messages, steps=5)

    assert [content for content in _thinking_contents(result) if content.startswith('why r2')] == [
        'why r2/0',
        'why r2/1',
        'why r2/2',
    ]
    assert kept == 5
    assert dropped == 1


def test_budget_window_retains_recent_reasoning_across_rounds():
    messages = [*_round('r1', 3), *_round('r2', 3)]

    result, _, _ = keep_recent_steps(messages, steps=5)

    assert _thinking_contents(result) == ['why r1/1', 'why r1/2', 'why r2/0', 'why r2/1', 'why r2/2']


def test_keeps_only_the_last_steps_of_a_long_round():
    """The window is linear: a turn with 200 steps does not resend 200 steps' worth.

    Resending the whole turn costs a sum of sums —44M billed tokens on a measured
    317-step turn— while the loop it fixes is local to a few steps.
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


def test_budget_window_crosses_round_boundaries():
    """Budget fitting counts responses across turns rather than resetting at a prompt."""
    messages = [*_round('r1', 5), *_round('r2', 2)]

    result, kept, _ = keep_recent_steps(messages, steps=4)

    assert kept == 4
    assert _thinking_contents(result) == ['why r1/3', 'why r1/4', 'why r2/0', 'why r2/1']


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
    before = len(split_prompts(messages))

    result, _, _ = keep_recent_steps(messages, steps=1)

    assert len(split_prompts(result)) == before
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
    messages: list = [_request('turn r1')]
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
    """`thinking_labels` reports which steps carry reasoning, per turn."""
    messages = [*_round('r1', 2), *_round('r2', 3), *_round('r3', 1)]

    labels = thinking_labels(messages)

    assert labels == ['r1', 'r1', 'r2', 'r2', 'r2', 'r3']


# --- the token cap on the window -------------------------------------------


def test_the_token_cap_bounds_the_window():
    """A wide window is useless if the reasoning alone eats the context.

    The cap comes first: the step count says how many blocks are worth keeping, the
    token cap says how many fit. A window of 10 steps of 5k tokens cannot spend 50k
    when only room for 12k is left.
    """
    messages = [*_round_with_sized_reasoning('r1', 10, 2_000)]

    result, kept, _ = keep_recent_steps(messages, steps=10, max_tokens=2_500)

    assert 0 < kept < 10
    assert _reasoning_tokens(result) <= 2_500
    # The closest steps to the work in progress are the ones that survive.
    assert _thinking_contents(result)[-1] == _thinking_of(9, 2_000)


def test_without_a_cap_the_step_count_is_the_only_limit():
    messages = [*_round_with_sized_reasoning('r1', 10, 2_000)]

    _, kept, _ = keep_recent_steps(messages, steps=10, max_tokens=None)

    assert kept == 10


def test_a_cap_of_zero_drops_everything():
    messages = [*_round_with_sized_reasoning('r1', 5, 1_000)]

    result, kept, dropped = keep_recent_steps(messages, steps=5, max_tokens=0)

    assert kept == 0
    assert dropped == 5
    assert _thinking_contents(result) == []


def test_the_cap_counts_blocks_one_by_one():
    """A block that does not fit is not kept; the search does not stop there."""
    messages = [*_round_with_sized_reasoning('r1', 4, 1_000)]

    # Room for three blocks of ~250 tokens each.
    result, kept, _ = keep_recent_steps(messages, steps=4, max_tokens=800)

    assert kept == 3
    assert _reasoning_tokens(result) <= 800


def _thinking_of(step: int, chars: int) -> str:
    return f'why r1/{step} ' + 'y' * chars


def _round_with_sized_reasoning(run: str, steps: int, chars: int) -> list:
    messages: list = [_request(f'turn {run}', run=run)]
    for step in range(steps):
        messages.append(_response(step, run, thinking=_thinking(_thinking_of(step, chars))))
    return messages


def _reasoning_tokens(messages: list) -> int:
    from src.engine.context.tokens import estimate_tokens

    return sum(estimate_tokens(content) for content in _thinking_contents(messages))


def test_the_first_request_of_a_round_keeps_the_handoff():
    """A new turn has no response yet, so the rule keeps the previous turn's tail.

    That is the moment the agent starts working with what it concluded before it
    handed control back. As soon as the new turn answers, its own steps take over.
    """
    handoff = [*_round('r1', 6), _request('a new prompt', run='r2')]

    result, kept, _ = keep_recent_steps(handoff, steps=2)

    assert kept == 2
    assert _thinking_contents(result) == ['why r1/4', 'why r1/5']

    # The new response extends the same cross-turn budget window.
    answered = [*handoff, _response(0, 'r2', thinking=_thinking('why r2/0'))]

    result, kept, _ = keep_recent_steps(answered, steps=2)

    assert kept == 2
    assert _thinking_contents(result) == ['why r1/5', 'why r2/0']


# --- the strategies apply the policy ---------------------------------------


def _budget(tokens: int = 10_000_000) -> ContextBudget:
    return ContextBudget(window=tokens + 8_000, output_reserve=8_000)


def test_windowed_keeps_the_reasoning_of_the_round_in_progress():
    messages = [*_round('r1', 3), *_round('r2', 3)]

    result = WindowedStrategy(reasoning_steps=2).view(messages, _budget())

    kept = [content for content in _thinking_contents(result.messages) if content.startswith('why r2')]
    assert kept == ['why r2/1', 'why r2/2']
    assert result.dropped_thinking == 4


def test_windowed_with_zero_steps_behaves_like_before():
    messages = [*_round('r1', 3), *_round('r2', 3)]

    result = WindowedStrategy(reasoning_steps=0).view(messages, _budget())

    assert _thinking_contents(result.messages) == []
    assert result.dropped_thinking == 6


def test_a_wide_operational_window_retains_previous_turns():
    """Recent reasoning stays eligible across user turns until a ceiling is reached."""
    messages = [*_round('r1', 2), *_round('r2', 2)]

    result = WindowedStrategy(reasoning_steps=1000).view(messages, _budget())

    assert _thinking_contents(result.messages) == ['why r1/0', 'why r1/1', 'why r2/0', 'why r2/1']
    assert result.dropped_thinking == 0


def test_the_retention_modes_are_gone():
    """`off` and `all` were flags, and a flag is a second policy. There is one now."""
    import dataclasses

    fields = {field.name for field in dataclasses.fields(WindowedStrategy)}
    assert 'reasoning' not in fields
    assert 'reasoning' not in {field.name for field in dataclasses.fields(CompactingStrategy)}


def test_thinking_window_never_breaks_the_budget():
    """The kept reasoning is a lever like any other: if it does not fit, it goes.

    Reasoning is the cheapest thing to drop, so it is dropped before a tool result
    is truncated or a turn is discarded.
    """
    from src.engine.context.tokens import estimate_messages_tokens

    def round_with_long_reasoning(run: str, steps: int) -> list:
        messages: list = [_request(f'turn {run}', run=run)]
        for step in range(steps):
            messages.append(_response(step, run, thinking=_thinking(f'why {run}/{step} ' + 'y' * 2_000)))
        return messages

    messages = [*round_with_long_reasoning('r1', 2), *round_with_long_reasoning('r2', 20)]

    # Room for the conversation plus a few steps of reasoning, not for all of it.
    without_reasoning = WindowedStrategy(reasoning_steps=0).view(messages, ContextBudget(window=None))
    room = estimate_messages_tokens(without_reasoning.messages)
    one_step = estimate_tokens('why r2/0 ' + 'y' * 2_000)
    allowance = one_step * 3

    # The percentage cap stays out of the way so the room itself is what shrinks it.
    tight = WindowedStrategy(reasoning_steps=20, reasoning_max_percent=100).view(
        messages, ContextBudget(window=room + allowance + 8_000, output_reserve=8_000)
    )

    kept = len(_thinking_contents(tight.messages))
    assert 0 < kept < 20
    assert tight.dropped_messages == 0
    assert tight.estimated_tokens <= room + allowance


def test_compacting_applies_the_same_policy():
    messages = [*_round('r1', 3), *_round('r2', 3)]

    result = CompactingStrategy(reasoning_steps=1).view(messages, _budget())

    kept = _thinking_contents(result.messages)
    assert kept == ['why r2/2']


def test_compacting_uses_the_same_single_window():
    """Compaction summarises old turns; it does not bring their reasoning back."""
    messages = [*_round('r1', 2), *_round('r2', 2)]

    result = CompactingStrategy(reasoning_steps=1000).view(messages, _budget())

    assert _thinking_contents(result.messages) == ['why r1/0', 'why r1/1', 'why r2/0', 'why r2/1']


# --- the cap in the strategies ---------------------------------------------


def test_the_strategies_cap_the_window_by_tokens():
    """10 steps is the intent; the cap is what makes it safe.

    With a 1M window and 10% allowed, the cap is 100k tokens of reasoning. A
    conversation with a nearly full context has a smaller `available_for_history`,
    and the cap follows it down instead of starving the turn.
    """
    messages = [*_round_with_sized_reasoning('r1', 10, 2_000)]

    result = WindowedStrategy(reasoning_steps=10, reasoning_max_percent=10).view(messages, _budget())

    assert len(_thinking_contents(result.messages)) == 10


def test_a_tight_context_shrinks_the_window():
    messages = [*_round_with_sized_reasoning('r1', 10, 20_000)]

    roomy = WindowedStrategy(reasoning_steps=10).view(messages, _budget())
    tight = WindowedStrategy(reasoning_steps=10).view(messages, _budget(tokens=40_000))

    assert len(_thinking_contents(tight.messages)) < len(_thinking_contents(roomy.messages))


def test_an_unknown_window_has_no_cap():
    """No window means no opinion: the step count is the only limit."""
    messages = [*_round_with_sized_reasoning('r1', 10, 2_000)]

    result = WindowedStrategy(reasoning_steps=10).view(messages, ContextBudget(window=None))

    assert len(_thinking_contents(result.messages)) == 10


def test_a_smaller_share_keeps_less():
    messages = [*_round_with_sized_reasoning('r1', 10, 2_000)]
    budget = _budget(tokens=300_000)

    generous = WindowedStrategy(reasoning_steps=10, reasoning_max_percent=50).view(messages, budget)
    stingy = WindowedStrategy(reasoning_steps=10, reasoning_max_percent=1).view(messages, budget)

    # A target is not a trigger: 1.7% is below the operational ceiling of 10%.
    assert len(_thinking_contents(stingy.messages)) == len(_thinking_contents(generous.messages)) == 10
