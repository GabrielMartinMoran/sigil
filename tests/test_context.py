"""Tests de la gestión de contexto: medición, bloques, scope y estrategias."""

from __future__ import annotations

import asyncio
import tempfile
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
from pydantic_ai.usage import RunUsage

from src.engine.context import (
    CompactingStrategy,
    ContextBudget,
    ContextMeter,
    ContextViewCapability,
    PassthroughStrategy,
    ScopeSink,
    ScopeTracker,
    WindowedStrategy,
    WorkspaceInstructions,
    estimate_tokens,
    measured_tokens,
    projected_tokens,
    render_report,
    split_turns,
)

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
    """Un turno completo: prompt, tool call, tool result, respuesta final."""
    return [
        _user(f'pedido {index}'),
        _tool_call('read_file', {'path': f'f{index}.py'}, f'c{index}'),
        _tool_return('read_file', 'x' * result_chars, f'c{index}'),
        _assistant(f'listo {index}', thinking=thinking),
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
    assert measured_tokens([_user('hola')]) is None


def test_projected_tokens_adds_trailing_messages():
    base = _turn(1)
    measured = measured_tokens(base)
    assert measured is not None

    with_trailing = [*base, _user('otra cosa')]
    projected = projected_tokens(with_trailing)
    assert projected is not None
    assert projected > measured


def test_projected_tokens_estimates_when_no_response():
    projected = projected_tokens([_user('hola mundo')])
    assert projected is not None and projected > 0


# --- presupuesto -----------------------------------------------------------


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


# --- turnos ----------------------------------------------------------------


def test_split_turns_groups_by_user_prompt():
    messages = [*_turn(1), *_turn(2), *_turn(3)]
    turns = split_turns(messages)
    assert len(turns) == 3
    assert all(len(turn) == 4 for turn in turns)


def test_split_turns_keeps_leading_messages_without_prompt():
    messages = [_assistant('suelto'), *_turn(1)]
    turns = split_turns(messages)
    assert len(turns) == 2
    assert turns[0] == [messages[0]]


# --- estrategias -----------------------------------------------------------


def test_passthrough_returns_everything():
    messages = [*_turn(1), *_turn(2)]
    result = PassthroughStrategy().view(messages, ContextBudget(window=None))
    assert result.messages == messages
    assert not result.changed


def test_windowed_drops_thinking():
    messages = [*_turn(1, thinking='razonamiento largo'), *_turn(2, thinking='más razonamiento')]
    result = WindowedStrategy().view(messages, ContextBudget(window=None))
    assert result.dropped_thinking == 2
    assert not any(isinstance(part, ThinkingPart) for message in result.messages for part in message.parts)


def test_windowed_keeps_thinking_when_asked():
    messages = [*_turn(1, thinking='razonamiento')]
    result = WindowedStrategy(keep_thinking=True).view(messages, ContextBudget(window=None))
    assert result.dropped_thinking == 0
    assert any(isinstance(part, ThinkingPart) for message in result.messages for part in message.parts)


def test_windowed_truncates_only_old_tool_results():
    messages = [*_turn(1, result_chars=20_000), *_turn(2, result_chars=20_000), *_turn(3, result_chars=20_000)]
    result = WindowedStrategy(keep_recent_turns=1, max_tool_result_chars=1_000).view(
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
    # El turno más reciente queda intacto.
    assert len(returns[-1].content) == 20_000
    assert 'truncated' in returns[0].content


def test_windowed_keeps_tool_call_and_return_paired():
    """Un `ToolReturnPart` sin su `ToolCallPart` es un 400 del provider."""
    messages = [*_turn(1, result_chars=20_000), *_turn(2), *_turn(3)]
    result = WindowedStrategy(keep_recent_turns=1, max_tool_result_chars=100).view(messages, ContextBudget(window=None))
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
    result = WindowedStrategy(keep_recent_turns=4).view(messages, ContextBudget(window=100, output_reserve=0))
    assert result.dropped_messages > 0
    assert len(result.messages) < len(messages)
    # Siempre queda al menos un turno.
    assert result.messages


def test_windowed_never_drops_the_last_turn():
    messages = [*_turn(1, result_chars=50_000)]
    result = WindowedStrategy(keep_recent_turns=1).view(messages, ContextBudget(window=10, output_reserve=0))
    assert result.messages


def test_compacting_collapses_old_turns_into_summary():
    messages = [*_turn(1), *_turn(2), *_turn(3), *_turn(4)]
    result = CompactingStrategy(keep_recent_turns=2).view(messages, ContextBudget(window=None))

    assert result.compacted_turns == 2
    first = result.messages[0]
    assert isinstance(first, ModelRequest)
    compaction = [part for part in first.parts if isinstance(part, CompactionPart)]
    assert len(compaction) == 1
    assert 'pedido 1' in compaction[0].content
    assert 'read_file' in compaction[0].content
    # Los turnos recientes quedan enteros.
    assert len(result.messages) == 1 + 2 * 4


def test_compacting_keeps_summary_when_over_budget():
    messages = [*_turn(1), *_turn(2), *_turn(3), *_turn(4)]
    result = CompactingStrategy(keep_recent_turns=2).view(messages, ContextBudget(window=50, output_reserve=0))
    assert isinstance(result.messages[0], ModelRequest)
    assert any(isinstance(part, CompactionPart) for part in result.messages[0].parts)


def test_compacting_without_old_turns_is_passthrough():
    messages = [*_turn(1), *_turn(2)]
    result = CompactingStrategy(keep_recent_turns=5).view(messages, ContextBudget(window=None))
    assert result.compacted_turns == 0
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


# --- bloques de workspace --------------------------------------------------


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
        path.write_text('uno')
        workspace = WorkspaceInstructions(root)
        assert workspace.resolve([root])[0].content == 'uno'

        path.write_text('dos, más largo')
        assert workspace.resolve([root])[0].content == 'dos, más largo'


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


def test_meter_ignores_invalid_window():
    meter = ContextMeter(window=0)
    assert meter.window is None
    meter.set_window(-5)
    assert meter.window is None


# --- capability ------------------------------------------------------------


def test_capability_is_not_destructive():
    """La vista cambia el request, no el historial persistente."""
    from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.tools import RunContext

    messages = [*_turn(1, thinking='razonamiento'), *_turn(2, thinking='más')]
    meter = ContextMeter(window=100_000)
    capability = ContextViewCapability(strategy=WindowedStrategy(), meter=meter)

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

    # El request perdió el razonamiento...
    assert not any(isinstance(part, ThinkingPart) for message in result.messages for part in message.parts)
    # ...pero el historial persistente sigue intacto.
    assert any(isinstance(part, ThinkingPart) for message in ctx.messages for part in message.parts)
    assert capability.last_view is not None


# --- tool results que no son str -------------------------------------------


def _pydantic_turn(index: int, *, content_chars: int = 200_000) -> list[ModelMessage]:
    """Un turno cuyo tool result es un modelo pydantic, como todas las tools de Sigil."""
    from src.engine.tools.files import FileContent

    payload = FileContent(
        path=f'f{index}.py',
        size=content_chars,
        truncated=False,
        binary=False,
        encoding='utf-8',
        content='x' * content_chars,
    )
    return [
        _user(f'leé f{index}.py'),
        _tool_call('read_file', {'path': f'f{index}.py'}, f'c{index}'),
        ModelRequest(parts=[ToolReturnPart(tool_name='read_file', content=payload, tool_call_id=f'c{index}')]),
        _assistant(f'listo {index}'),
    ]


def test_tool_return_text_uses_model_response_str():
    """El tamaño se mide sobre lo que ve el modelo, no sobre el repr de Python."""
    from src.engine.context import tool_return_text

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
    """Regresión: comparar contra `isinstance(content, str)` dejaba el truncado sin efecto."""
    messages = [*_pydantic_turn(1), *_pydantic_turn(2), *_pydantic_turn(3)]
    result = WindowedStrategy(keep_recent_turns=1, max_tool_result_chars=1_000).view(
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
    # El turno más reciente queda intacto.
    assert len(returns[-1].model_response_str()) > 200_000


def test_windowed_never_truncates_pinned_tools():
    """El cuerpo de un skill es instrucción: truncarlo le saca la tarea al agente."""
    body = 'y' * 30_000
    messages = [
        _user('cargá el skill'),
        _tool_call('load_skill', {'name': 'clean-code'}, 's1'),
        ModelRequest(parts=[ToolReturnPart(tool_name='load_skill', content=body, tool_call_id='s1')]),
        _assistant('cargado'),
        *_turn(2),
        *_turn(3),
    ]
    result = WindowedStrategy(keep_recent_turns=1, max_tool_result_chars=1_000).view(
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
        _user('cargá el skill'),
        _tool_call('load_skill', {'name': 'clean-code'}, 's1'),
        ModelRequest(parts=[ToolReturnPart(tool_name='load_skill', content=body, tool_call_id='s1')]),
        _assistant('cargado'),
        *_turn(2),
        *_turn(3),
        *_turn(4),
    ]
    result = CompactingStrategy(keep_recent_turns=1).view(messages, ContextBudget(window=None))

    # El turno del skill sobrevive entero; los demás turnos viejos se compactan.
    assert any(
        isinstance(part, ToolReturnPart) and part.tool_name == 'load_skill' and part.content == body
        for message in result.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )
    assert result.compacted_turns == 2


def test_fit_to_budget_prefers_dropping_unpinned_turns():
    """Si hay que elegir, se pierde un read_file viejo antes que las instrucciones."""
    body = 'y' * 4_000
    messages = [
        _user('cargá el skill'),
        _tool_call('load_skill', {'name': 'x'}, 's1'),
        ModelRequest(parts=[ToolReturnPart(tool_name='load_skill', content=body, tool_call_id='s1')]),
        _assistant('cargado'),
        *_turn(2, result_chars=4_000),
        *_turn(3, result_chars=4_000),
    ]
    from src.engine.context import estimate_messages_tokens

    # Presupuesto justo para que alcance con descartar el turno sin instrucciones.
    total = estimate_messages_tokens(messages)
    unpinned = estimate_messages_tokens(_turn(2, result_chars=4_000))
    window = total - unpinned + 50

    result = WindowedStrategy(keep_recent_turns=1).view(messages, ContextBudget(window=window, output_reserve=0))

    assert result.dropped_messages == len(_turn(2, result_chars=4_000))
    assert any(
        isinstance(part, ToolReturnPart) and part.tool_name == 'load_skill'
        for message in result.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )
