"""Tests for context management: measurement, blocks, scope and strategies."""

from __future__ import annotations

import asyncio
import tempfile
from decimal import Decimal
from pathlib import Path

from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage, RunUsage

from src.engine.context.budget import ContextBudget
from src.engine.context.capability import ContextViewCapability
from src.engine.context.instructions.workspace_instructions import WorkspaceInstructions
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.meter.render import render_report
from src.engine.context.scope import ScopeSink, ScopeTracker
from src.engine.context.strategy.compacting import CompactingStrategy
from src.engine.context.strategy.passthrough import PassthroughStrategy
from src.engine.context.strategy.turns import split_prompts
from src.engine.context.strategy.windowed import WindowedStrategy
from src.engine.context.tokens import estimate_tokens, measured_tokens, projected_tokens
from src.engine.context.working_set import render_working_set, touched_files

# --- helpers ---------------------------------------------------------------


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str, *, thinking: str | None = None) -> ModelResponse:
    parts: list = []
    if thinking:
        parts.append(ThinkingPart(content=thinking))
    parts.append(TextPart(content=text))
    return ModelResponse(parts=parts)


def _tool_call(name: str, args: dict, call_id: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args, tool_call_id=call_id)])


def _tool_return(name: str, content: str, call_id: str, *, outcome: str = 'success') -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart(tool_name=name, content=content, tool_call_id=call_id, outcome=outcome)])


def _turn(index: int, *, result_chars: int = 100, thinking: str | None = None) -> list[ModelMessage]:
    """A whole turn: prompt, tool call, tool result, final answer."""
    return [
        _user(f'request {index}'),
        _tool_call('read_file', {'path': f'f{index}.py'}, f'c{index}'),
        _tool_return('read_file', 'x' * result_chars, f'c{index}'),
        _assistant(f'done {index}', thinking=thinking),
    ]


# --- tokens ----------------------------------------------------------------


def test_estimate_tokens_is_zero_for_empty():
    assert estimate_tokens('') == 0
    assert estimate_tokens('a') == 1
    assert estimate_tokens('a' * 400) == 100


def test_measured_tokens_uses_last_response():
    messages = [*_turn(1), *_turn(2)]
    measured = measured_tokens(messages)
    assert measured is not None
    assert measured >= 0


def test_measured_tokens_none_without_response():
    assert measured_tokens([_user('hello')]) is None


def test_projected_tokens_adds_trailing_messages():
    base = _turn(1)
    measured = measured_tokens(base)
    assert measured is not None

    with_trailing = [*base, _user('another thing')]
    projected = projected_tokens(with_trailing)
    assert projected is not None
    assert projected > measured


def test_projected_tokens_estimates_when_no_response():
    projected = projected_tokens([_user('hello world')])
    assert projected is not None and projected > 0


# --- instructions are not part of the history ------------------------------


def _with_instructions(text: str, instructions: str) -> ModelRequest:
    """A request that carries the rendered instructions, as every saved one does."""
    return ModelRequest(parts=[UserPromptPart(content=text)], instructions=instructions)


def test_estimate_ignores_replayed_instructions():
    """A saved request keeps its instructions, but they are not resent with the history.

    pydantic-ai sends the instructions of the request being made
    (`model_request_parameters.instruction_parts`); the ones recorded on earlier
    messages only document what that request was made with. Counting them made a
    three-turn conversation weigh 12x what it actually sent, and the surplus pushed
    the history over the budget, so whole turns were discarded.
    """
    from src.engine.context.tokens import estimate_message_tokens

    prompt = _user('hello')
    with_instructions = _with_instructions('hello', 'x' * 40_000)

    assert estimate_message_tokens(with_instructions) == estimate_message_tokens(prompt)


def test_estimate_history_does_not_grow_with_replayed_instructions():
    """Ten turns weigh the same whether or not their requests recorded instructions."""
    from src.engine.context.tokens import estimate_messages_tokens

    plain = [_turn(index) for index in range(10)]
    replayed = [
        [
            _with_instructions(f'request {index}', 'system prompt ' * 3_000) if position == 0 else message
            for position, message in enumerate(turn)
        ]
        for index, turn in enumerate(plain)
    ]

    flat_plain: list[ModelMessage] = [message for turn in plain for message in turn]
    flat_replayed: list[ModelMessage] = [message for turn in replayed for message in turn]

    assert estimate_messages_tokens(flat_replayed) == estimate_messages_tokens(flat_plain)


def test_turns_survive_when_only_replayed_instructions_are_oversized():
    """The regression: a long conversation loses its old turns to phantom tokens.

    The real session: 451 requests carrying 34,520 characters of instructions each
    (8,630 tokens, replayed 451 times). The content fits in the window; the
    recorded instructions, which are never resent, made the estimate 12x its real
    weight at the point where the trimming ran and the strategy discarded whole
    turns.
    """
    from src.engine.context.tokens import estimate_tokens, part_text

    body = 'system prompt ' * 1_500  # ~22k chars of instructions per request
    messages: list[ModelMessage] = []
    for index in range(10):
        messages.append(_with_instructions(f'request {index}', body))
        messages.append(_assistant(f'done {index}'))

    # What actually travels: the parts, without the replayed instructions.
    content_tokens = sum(estimate_tokens(part_text(part)) for message in messages for part in message.parts)
    budget = ContextBudget(window=content_tokens * 2, output_reserve=0)

    result = WindowedStrategy(keep_recent_prompts=3).view(messages, budget)

    assert result.dropped_messages == 0
    assert len(result.messages) == len(messages)


def test_projected_tokens_ignores_replayed_instructions():
    """The panel's number does not take the instructions of past requests into account."""
    from src.engine.context.tokens import projected_tokens

    body = 'system prompt ' * 1_500
    messages: list[ModelMessage] = [
        _with_instructions('first', body),
        _assistant('done'),
        _with_instructions('second', body),
        _assistant('done'),
        _with_instructions('third', body),
    ]

    projected = projected_tokens(messages)

    assert projected is not None
    # Only the three prompts and two answers travel: ~10 tokens, not 50k.
    assert projected < 100


# --- budget ----------------------------------------------------------------


def test_budget_subtracts_fixed_costs():
    budget = ContextBudget(window=100_000, tools=5_000, instructions=2_000, output_reserve=8_000)
    assert budget.fixed == 7_000
    assert budget.available_for_history == 85_000
    assert budget.fits(80_000) is True
    assert budget.fits(90_000) is False


def test_budget_without_window_has_no_limit():
    budget = ContextBudget(window=None)
    assert budget.available_for_history is None
    assert budget.fits(10**9) is None


def test_budget_never_negative():
    budget = ContextBudget(window=1_000, tools=900, instructions=900, output_reserve=8_000)
    assert budget.available_for_history == 0


# --- turns -----------------------------------------------------------------


def test_split_prompts_groups_by_user_prompt():
    messages = [*_turn(1), *_turn(2), *_turn(3)]
    turns = split_prompts(messages)
    assert len(turns) == 3
    assert all(len(turn) == 4 for turn in turns)


def test_split_prompts_keeps_leading_messages_without_prompt():
    messages = [_assistant('loose'), *_turn(1)]
    turns = split_prompts(messages)
    assert len(turns) == 2
    assert turns[0] == [messages[0]]


# --- strategies ------------------------------------------------------------


def test_passthrough_returns_everything():
    messages = [*_turn(1), *_turn(2)]
    result = PassthroughStrategy().view(messages, ContextBudget(window=None))
    assert result.messages == messages
    assert not result.changed


def test_windowed_drops_thinking():
    """Past turns' reasoning is a record, not working memory: it goes."""
    messages = [*_turn(1, thinking='long reasoning'), *_turn(2, thinking='more reasoning')]
    result = WindowedStrategy(reasoning='off').view(messages, ContextBudget(window=None))
    assert result.dropped_thinking == 2
    assert not any(isinstance(part, ThinkingPart) for message in result.messages for part in message.parts)


def test_windowed_keeps_the_reasoning_of_the_round_in_progress():
    """Only the current turn's reasoning is kept, and only its last steps."""
    messages = [*_turn(1, thinking='from a finished turn'), *_turn(2, thinking='current turn')]

    result = WindowedStrategy(reasoning='recent', reasoning_steps=1).view(messages, ContextBudget(window=None))

    contents = [part.content for message in result.messages for part in message.parts if isinstance(part, ThinkingPart)]
    assert contents == ['current turn']
    assert result.dropped_thinking == 1


def test_windowed_keeps_thinking_when_asked():
    messages = [*_turn(1, thinking='reasoning')]
    result = WindowedStrategy(reasoning='all').view(messages, ContextBudget(window=None))
    assert result.dropped_thinking == 0
    assert any(isinstance(part, ThinkingPart) for message in result.messages for part in message.parts)


def test_windowed_truncates_only_old_tool_results():
    messages = [*_turn(1, result_chars=20_000), *_turn(2, result_chars=20_000), *_turn(3, result_chars=20_000)]
    result = WindowedStrategy(keep_recent_prompts=1, max_tool_result_chars=1_000).view(
        messages, ContextBudget(window=None)
    )
    assert result.truncated_results == 2

    returns = [
        part
        for message in result.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    # The most recent turn stays intact.
    assert len(returns[-1].content) == 20_000
    assert 'truncated' in returns[0].content


def test_windowed_keeps_tool_call_and_return_paired():
    """Un `ToolReturnPart` sin su `ToolCallPart` es un 400 del provider."""
    messages = [*_turn(1, result_chars=20_000), *_turn(2), *_turn(3)]
    result = WindowedStrategy(keep_recent_prompts=1, max_tool_result_chars=100).view(
        messages, ContextBudget(window=None)
    )
    call_ids = {
        part.tool_call_id
        for message in result.messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    }
    return_ids = {
        part.tool_call_id
        for message in result.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }
    assert return_ids <= call_ids


def test_windowed_drops_oldest_turns_to_fit_budget():
    messages = [*_turn(1), *_turn(2), *_turn(3), *_turn(4)]
    result = WindowedStrategy(keep_recent_prompts=4).view(messages, ContextBudget(window=100, output_reserve=0))
    assert result.dropped_messages > 0
    assert len(result.messages) < len(messages)
    # There is always at least one turn left.
    assert result.messages


def test_windowed_never_drops_the_last_turn():
    messages = [*_turn(1, result_chars=50_000)]
    result = WindowedStrategy(keep_recent_prompts=1).view(messages, ContextBudget(window=10, output_reserve=0))
    assert result.messages


def test_compacting_collapses_old_turns_into_summary():
    messages = [*_turn(1), *_turn(2), *_turn(3), *_turn(4)]
    result = CompactingStrategy(keep_recent_prompts=2).view(messages, ContextBudget(window=None))

    assert result.compacted_prompts == 2
    first = result.messages[0]
    assert isinstance(first, ModelRequest)
    compaction = [part for part in first.parts if isinstance(part, CompactionPart)]
    assert len(compaction) == 1
    assert 'request 1' in compaction[0].content
    assert 'read_file' in compaction[0].content
    # Recent turns stay whole.
    assert len(result.messages) == 1 + 2 * 4


def test_compacting_keeps_summary_when_over_budget():
    messages = [*_turn(1), *_turn(2), *_turn(3), *_turn(4)]
    result = CompactingStrategy(keep_recent_prompts=2).view(messages, ContextBudget(window=50, output_reserve=0))
    assert isinstance(result.messages[0], ModelRequest)
    assert any(isinstance(part, CompactionPart) for part in result.messages[0].parts)


def test_compacting_without_old_turns_is_passthrough():
    messages = [*_turn(1), *_turn(2)]
    result = CompactingStrategy(keep_recent_prompts=5).view(messages, ContextBudget(window=None))
    assert result.compacted_prompts == 0
    assert len(result.messages) == len(messages)


# --- scope -----------------------------------------------------------------


def test_scope_always_contains_root():
    with tempfile.TemporaryDirectory() as tmp:
        tracker = ScopeTracker(tmp)
        assert tracker.scopes() == (Path(tmp).resolve(),)
        assert tracker.relative_scopes() == ('.',)


def test_scope_adds_child_chain():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'a' / 'b').mkdir(parents=True)
        tracker = ScopeTracker(root)
        tracker.observe('read_file', {'path': 'a/b/file.py'})

        relative = tracker.relative_scopes()
        assert relative == ('.', 'a', 'a/b')


def test_scope_ignores_siblings_until_touched():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'a').mkdir()
        (root / 'b').mkdir()
        tracker = ScopeTracker(root)
        tracker.observe('read_file', {'path': 'a/x.py'})
        assert 'b' not in tracker.relative_scopes()

        tracker.observe('read_file', {'path': 'b/y.py'})
        assert 'b' in tracker.relative_scopes()


def test_scope_ignores_paths_outside_workspace():
    with tempfile.TemporaryDirectory() as tmp:
        tracker = ScopeTracker(tmp)
        tracker.observe('read_file', {'path': '/etc/passwd'})
        assert tracker.relative_scopes() == ('.',)


def test_scope_ignores_tools_without_paths():
    with tempfile.TemporaryDirectory() as tmp:
        tracker = ScopeTracker(tmp)
        assert tracker.observe('web_search', {'query': 'x'}) is False
        assert tracker.observe('run', {'cmds': ['ls']}) is False


def test_scope_reset_returns_to_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'a').mkdir()
        tracker = ScopeTracker(root)
        tracker.observe('read_file', {'path': 'a/x.py'})
        tracker.reset()
        assert tracker.relative_scopes() == ('.',)


def test_scope_sink_feeds_tracker():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'a').mkdir()
        tracker = ScopeTracker(root)
        sink = ScopeSink(tracker)

        class _Event:
            def __init__(self) -> None:
                self.phase = 'start'
                self.tool_name = 'read_file'
                self.args = {'path': 'a/x.py'}

        sink(_Event())
        assert 'a' in tracker.relative_scopes()


# --- workspace blocks ------------------------------------------------------


def test_workspace_prefers_agents_over_claude():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'AGENTS.md').write_text('agents')
        (root / 'CLAUDE.md').write_text('claude')
        workspace = WorkspaceInstructions(root)
        files = workspace.resolve([root])
        assert len(files) == 1
        assert files[0].content == 'agents'


def test_workspace_falls_back_to_claude():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'CLAUDE.md').write_text('claude')
        workspace = WorkspaceInstructions(root)
        files = workspace.resolve([root])
        assert len(files) == 1
        assert files[0].content == 'claude'


def test_workspace_orders_parent_before_child():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'a').mkdir()
        (root / 'AGENTS.md').write_text('root')
        (root / 'a' / 'AGENTS.md').write_text('child')
        workspace = WorkspaceInstructions(root)
        files = workspace.resolve([root / 'a', root])
        assert [file.content for file in files] == ['root', 'child']


def test_workspace_block_uses_append_policy():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'AGENTS.md').write_text('root')
        block = WorkspaceInstructions(root).block([root])
        assert block is not None
        assert block.name == 'workspace'
        assert block.on_change == 'append'


def test_workspace_block_is_none_without_files():
    with tempfile.TemporaryDirectory() as tmp:
        assert WorkspaceInstructions(tmp).block([Path(tmp)]) is None


def test_workspace_truncates_large_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'AGENTS.md').write_text('x' * 5_000)
        workspace = WorkspaceInstructions(root, max_file_chars=1_000)
        files = workspace.resolve([root])
        assert files[0].truncated is True
        assert len(files[0].content) == 1_000


def test_workspace_respects_total_limit():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'a').mkdir()
        (root / 'AGENTS.md').write_text('x' * 800)
        (root / 'a' / 'AGENTS.md').write_text('y' * 800)
        workspace = WorkspaceInstructions(root, max_file_chars=1_000, max_total_chars=1_000)
        files = workspace.resolve([root, root / 'a'])
        assert sum(len(file.content) for file in files) <= 1_000


def test_workspace_cache_invalidates_on_change():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / 'AGENTS.md'
        path.write_text('one')
        workspace = WorkspaceInstructions(root)
        assert workspace.resolve([root])[0].content == 'one'

        path.write_text('two, longer')
        assert workspace.resolve([root])[0].content == 'two, longer'


# --- meter -----------------------------------------------------------------


def test_meter_snapshot_and_report():
    meter = ContextMeter(window=100_000, output_reserve=1_000)
    meter.set_overhead(instructions=500, tools=1_500)
    messages = [*_turn(1), *_turn(2)]

    snapshot = meter.snapshot(messages)
    assert snapshot.window == 100_000
    assert snapshot.budget.fixed == 2_000
    assert snapshot.budget.available_for_history == 97_000

    report = meter.report(messages)
    assert report.instructions == 500
    assert report.tools == 1_500
    assert len(report.turns) == 2
    assert report.heaviest_tool_results

    text = render_report(report)
    assert 'Context' in text
    assert 'Budget' in text
    assert 'Turns' in text


def test_the_report_carries_the_bill_of_the_session():
    """`/context` shows the money next to the tokens: one is the window, the other its price."""
    messages = [
        _user('hi'),
        ModelResponse(
            parts=[TextPart(content='ok')],
            usage=RequestUsage(input_tokens=1_000, output_tokens=100),
            model_name='claude-sonnet-4-5',
            provider_details={'cost': 0.042},
        ),
    ]

    report = ContextMeter(window=200_000).report(messages)
    text = render_report(report)

    assert report.cost.total == Decimal('0.042')
    assert 'Cost' in text
    assert 'total            : $0.0420' in text
    assert '1 reported by the provider' in text
    # A reported amount is a charge, not an estimate: no note, no floor.
    assert 'at list price' not in text
    assert 'floor' not in text


def test_the_report_marks_an_estimated_total():
    messages = [ModelResponse(parts=[TextPart(content='ok')], usage=RequestUsage(input_tokens=10, output_tokens=1))]
    messages[0].usage.cost = Decimal('0.0005')

    text = render_report(ContextMeter(window=200_000).report(messages))

    assert 'total            : $0.0005 (at list price)' in text


def test_the_report_marks_a_total_that_is_a_floor():
    messages = [ModelResponse(parts=[TextPart(content='ok')])]

    text = render_report(ContextMeter(window=200_000).report(messages))

    assert 'total            : ?' in text
    assert 'floor' in text


def test_meter_ignores_invalid_window():
    meter = ContextMeter(window=0)
    assert meter.window is None
    meter.set_window(-5)
    assert meter.window is None


# --- capability ------------------------------------------------------------


def test_capability_is_not_destructive():
    """The view changes the request, not the persistent history."""
    from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.tools import RunContext

    messages = [*_turn(1, thinking='reasoning'), *_turn(2, thinking='more')]
    meter = ContextMeter(window=100_000)
    capability = ContextViewCapability(strategy=WindowedStrategy(reasoning='recent', reasoning_steps=1), meter=meter)

    model = TestModel()
    ctx = RunContext(deps=None, model=model, usage=RunUsage())
    ctx.messages[:] = list(messages)

    request_context = ModelRequestContext(
        model=model,
        messages=list(messages),
        model_settings=None,
        model_request_parameters=ModelRequestParameters(),
    )

    result = asyncio.run(capability.before_model_request(ctx, request_context))

    # The request lost the reasoning of the finished steps...
    kept = [part for message in result.messages for part in message.parts if isinstance(part, ThinkingPart)]
    assert len(kept) == 1
    # ...but the persistent history stays intact.
    assert any(isinstance(part, ThinkingPart) for message in ctx.messages for part in message.parts)
    assert capability.last_view is not None


# --- tool results that are not str -----------------------------------------


def _pydantic_turn(index: int, *, content_chars: int = 200_000) -> list[ModelMessage]:
    """A turn whose tool result is a pydantic model, like every Sigil tool."""
    from src.engine.files.models.file_content import FileContent

    payload = FileContent(
        path=f'f{index}.py',
        size=content_chars,
        truncated=False,
        binary=False,
        encoding='utf-8',
        content='x' * content_chars,
    )
    return [
        _user(f'read f{index}.py'),
        _tool_call('read_file', {'path': f'f{index}.py'}, f'c{index}'),
        ModelRequest(parts=[ToolReturnPart(tool_name='read_file', content=payload, tool_call_id=f'c{index}')]),
        _assistant(f'done {index}'),
    ]


def test_tool_return_text_uses_model_response_str():
    """The size is measured on what the model sees, not on the Python repr."""
    from src.engine.context.tokens import tool_return_text

    turn = _pydantic_turn(1, content_chars=5_000)
    part = next(
        part
        for message in turn
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    )
    text = tool_return_text(part)
    assert len(text) > 5_000
    assert text.startswith('{')


def test_windowed_truncates_pydantic_tool_results():
    """Regression: comparing against `isinstance(content, str)` left the truncation without effect."""
    messages = [*_pydantic_turn(1), *_pydantic_turn(2), *_pydantic_turn(3)]
    result = WindowedStrategy(keep_recent_prompts=1, max_tool_result_chars=1_000).view(
        messages, ContextBudget(window=None)
    )
    assert result.truncated_results == 2

    returns = [
        part
        for message in result.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert len(returns[0].model_response_str()) < 1_500
    # The most recent turn stays intact.
    assert len(returns[-1].model_response_str()) > 200_000


def test_windowed_never_truncates_pinned_tools():
    """A skill body is instruction: truncating it takes the task away from the agent."""
    body = 'y' * 30_000
    messages = [
        _user('load the skill'),
        _tool_call('load_skill', {'name': 'clean-code'}, 's1'),
        ModelRequest(parts=[ToolReturnPart(tool_name='load_skill', content=body, tool_call_id='s1')]),
        _assistant('loaded'),
        *_turn(2),
        *_turn(3),
    ]
    result = WindowedStrategy(keep_recent_prompts=1, max_tool_result_chars=1_000).view(
        messages, ContextBudget(window=None)
    )
    skill_return = next(
        part
        for message in result.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == 'load_skill'
    )
    assert skill_return.content == body


def test_compacting_keeps_pinned_turns_intact():
    body = 'y' * 10_000
    messages = [
        _user('load the skill'),
        _tool_call('load_skill', {'name': 'clean-code'}, 's1'),
        ModelRequest(parts=[ToolReturnPart(tool_name='load_skill', content=body, tool_call_id='s1')]),
        _assistant('loaded'),
        *_turn(2),
        *_turn(3),
        *_turn(4),
    ]
    result = CompactingStrategy(keep_recent_prompts=1).view(messages, ContextBudget(window=None))

    # The skill turn survives whole; the other old turns get compacted.
    assert any(
        isinstance(part, ToolReturnPart) and part.tool_name == 'load_skill' and part.content == body
        for message in result.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )
    assert result.compacted_prompts == 2


def test_fit_to_budget_prefers_dropping_unpinned_turns():
    """If you have to choose, an old read_file is lost before the instructions."""
    body = 'y' * 4_000
    messages = [
        _user('load the skill'),
        _tool_call('load_skill', {'name': 'x'}, 's1'),
        ModelRequest(parts=[ToolReturnPart(tool_name='load_skill', content=body, tool_call_id='s1')]),
        _assistant('loaded'),
        *_turn(2, result_chars=4_000),
        *_turn(3, result_chars=4_000),
    ]
    from src.engine.context.tokens import estimate_messages_tokens

    # Budget just enough to fit by discarding the turn without instructions.
    total = estimate_messages_tokens(messages)
    unpinned = estimate_messages_tokens(_turn(2, result_chars=4_000))
    window = total - unpinned + 50

    result = WindowedStrategy(keep_recent_prompts=1).view(messages, ContextBudget(window=window, output_reserve=0))

    assert result.dropped_messages == len(_turn(2, result_chars=4_000))
    assert any(
        isinstance(part, ToolReturnPart) and part.tool_name == 'load_skill'
        for message in result.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


# --- working set -----------------------------------------------------------


def test_touched_files_collects_paths_from_path_tools():
    messages = [
        _user('read and edit'),
        _tool_call('read_file', {'path': 'src/a.py'}, 'c1'),
        _tool_return('read_file', 'x', 'c1'),
        _tool_call('patch_file', {'path': 'src/a.py'}, 'c2'),
        _tool_return('patch_file', 'ok', 'c2'),
    ]
    files = touched_files(messages)
    assert [file.path for file in files] == ['src/a.py']
    assert files[0].tools == ('read_file', 'patch_file')
    assert files[0].written is True


def test_touched_files_ignores_tools_without_paths():
    messages = [
        _user('run something'),
        _tool_call('run', {'command': 'ls'}, 'c1'),
        _tool_return('run', 'a.py', 'c1'),
        _tool_call('web_search', {'query': 'x'}, 'c2'),
        _tool_return('web_search', 'y', 'c2'),
    ]
    assert touched_files(messages) == []


def test_touched_files_orders_by_most_recent_interaction():
    messages = [
        _user('one'),
        _tool_call('read_file', {'path': 'a.py'}, 'c1'),
        _tool_return('read_file', 'x', 'c1'),
        _user('two'),
        _tool_call('read_file', {'path': 'b.py'}, 'c2'),
        _tool_return('read_file', 'x', 'c2'),
        _user('three'),
        _tool_call('patch_file', {'path': 'a.py'}, 'c3'),
        _tool_return('patch_file', 'ok', 'c3'),
    ]
    files = touched_files(messages)
    # `a.py` was touched last, so it goes first even though it appeared earlier.
    assert [file.path for file in files] == ['a.py', 'b.py']
    assert files[0].tools == ('read_file', 'patch_file')


def test_touched_files_normalizes_relative_prefixes():
    messages = [
        _user('read'),
        _tool_call('read_file', {'path': './src/a.py'}, 'c1'),
        _tool_return('read_file', 'x', 'c1'),
        _tool_call('read_file', {'path': 'src/a.py'}, 'c2'),
        _tool_return('read_file', 'x', 'c2'),
    ]
    files = touched_files(messages)
    assert [file.path for file in files] == ['src/a.py']


def test_touched_files_parses_json_string_args():
    """pydantic-ai leaves `args` as a string when the model did not validate the schema."""
    messages = [
        _user('read'),
        ModelResponse(parts=[ToolCallPart(tool_name='read_file', args='{"path": "a.py"}', tool_call_id='c1')]),
        _tool_return('read_file', 'x', 'c1'),
    ]
    assert [file.path for file in touched_files(messages)] == ['a.py']


def test_touched_files_ignores_unparseable_args():
    messages = [
        _user('read'),
        ModelResponse(parts=[ToolCallPart(tool_name='read_file', args='{not json', tool_call_id='c1')]),
        _tool_return('read_file', 'x', 'c1'),
    ]
    assert touched_files(messages) == []


def test_touched_files_respects_limit():
    messages = [_user('muchos')]
    for index in range(5):
        messages.append(_tool_call('read_file', {'path': f'f{index}.py'}, f'c{index}'))
        messages.append(_tool_return('read_file', 'x', f'c{index}'))
    files = touched_files(messages, limit=2)
    assert [file.path for file in files] == ['f4.py', 'f3.py']


def test_render_working_set_is_empty_without_files():
    assert render_working_set([]) == ''


def test_compacting_summary_includes_working_set():
    messages = [*_turn(1), *_turn(2), *_turn(3), *_turn(4)]
    result = CompactingStrategy(keep_recent_prompts=2).view(messages, ContextBudget(window=None))

    summary = next(part.content for part in result.messages[0].parts if isinstance(part, CompactionPart))
    assert 'Files touched so far' in summary
    # The four turns touched f1..f4; the most recent one goes first.
    working_set = summary[summary.index('Files touched so far') :]
    assert working_set.index('f4.py') < working_set.index('f1.py')


def test_compacting_working_set_can_be_disabled():
    messages = [*_turn(1), *_turn(2), *_turn(3), *_turn(4)]
    result = CompactingStrategy(keep_recent_prompts=2, include_working_set=False).view(
        messages, ContextBudget(window=None)
    )
    summary = next(part.content for part in result.messages[0].parts if isinstance(part, CompactionPart))
    assert 'Files touched' not in summary


def test_compacting_without_compaction_has_no_working_set():
    """With no old turns there is no summary, and therefore no working set either."""
    messages = [*_turn(1), *_turn(2)]
    result = CompactingStrategy(keep_recent_prompts=5).view(messages, ContextBudget(window=None))
    assert not any(isinstance(part, CompactionPart) for message in result.messages for part in message.parts)
