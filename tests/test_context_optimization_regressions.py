import json

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.coordinator import apply, plan, policies_from
from src.engine.context.strategy.compacting import CompactingStrategy
from src.engine.context.strategy.windowed import WindowedStrategy
from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_result import QuestionResult
from src.engine.mcp.tool_models.prompt_message_info import PromptMessageInfo
from src.engine.mcp.tool_models.prompt_result_info import PromptResultInfo
from src.engine.shell.models.run_tool_output import RunToolOutput
from src.engine.skills.tool_models.script_run_result import ScriptRunResult
from src.engine.tooling.lod import GENERIC_LOD, ToolLOD, limit_for
from src.engine.tooling.lod_policies import ALL_POLICIES


def thoughts(n, run='a', size=4):
    return [ModelRequest(parts=[UserPromptPart(content='go')], run_id=run)] + [
        ModelResponse(parts=[ThinkingPart(content='x' * size), TextPart(content='ok')], run_id=run) for _ in range(n)
    ]


def count_thoughts(messages):
    return sum(isinstance(p, ThinkingPart) for m in messages for p in m.parts)


@pytest.mark.parametrize('strategy', [WindowedStrategy(), CompactingStrategy()])
def test_reasoning_waits_for_twenty_step_ceiling(strategy):
    result = strategy.view(thoughts(11), ContextBudget(window=None))
    assert not result.events
    assert count_thoughts(result.messages) == 11


@pytest.mark.parametrize('strategy', [WindowedStrategy(), CompactingStrategy()])
def test_reasoning_survives_across_turns(strategy):
    result = strategy.view(thoughts(3) + thoughts(1, run='b'), ContextBudget(window=None))
    assert count_thoughts(result.messages) == 4


def test_reasoning_waits_for_ten_percent_ceiling():
    result = WindowedStrategy().view(thoughts(1, size=2400), ContextBudget(window=10000, output_reserve=0))
    assert not result.events
    assert count_thoughts(result.messages) == 1  # 600 tokens = 6%, below trigger 10%


@pytest.mark.parametrize('level', [ToolLOD.LOD_1, ToolLOD.LOD_2, ToolLOD.LOD_3])
def test_generic_target_is_for_the_value_not_each_leaf(level):
    value = {'rows': ['x' * 90 for _ in range(1000)]}
    reduced = GENERIC_LOD.compact_output(value, level)
    assert len(json.dumps(reduced)) <= limit_for(level)


@pytest.mark.parametrize(
    'tool,value',
    [
        ('run', [RunToolOutput(returncode=17, stdout='partial', stderr='failure')]),
        (
            'run_skill_script',
            ScriptRunResult(skill='demo', script='run.py', returncode=17, stdout='partial', stderr='failure'),
        ),
    ],
)
@pytest.mark.parametrize('level', [ToolLOD.LOD_1, ToolLOD.LOD_2, ToolLOD.LOD_3])
def test_real_process_exit_status_survives(tool, value, level):
    reduced = ALL_POLICIES[tool].compact_output(value, level)
    item = reduced[0] if isinstance(reduced, list) else reduced
    assert item.get('returncode') == 17
    assert 'failure' in item.get('stderr', '')


@pytest.mark.parametrize('level', [ToolLOD.LOD_1, ToolLOD.LOD_2, ToolLOD.LOD_3])
def test_real_user_answers_survive(level):
    value = QuestionResult(answers=[QuestionAnswer(question='Deployment?', answer='Do not deploy')])
    reduced = ALL_POLICIES['ask_user'].compact_output(value, level)
    assert 'Do not deploy' in str(reduced)


@pytest.mark.parametrize('level', [ToolLOD.LOD_1, ToolLOD.LOD_2, ToolLOD.LOD_3])
def test_real_agents_table_survives(level):
    reduced = ALL_POLICIES['list_agents'].compact_output('id | agent | state\nabc123 | worker | failed', level)
    assert 'abc123' in str(reduced)
    assert 'failed' in str(reduced)


@pytest.mark.parametrize('level', [ToolLOD.LOD_1, ToolLOD.LOD_2, ToolLOD.LOD_3])
def test_mcp_prompt_preserves_some_message_content(level):
    value = PromptResultInfo(
        server='local', name='prompt', messages=[PromptMessageInfo(role='user', content='Critical context')]
    )
    reduced = ALL_POLICIES['get_mcp_prompt'].compact_output(value, level)
    assert 'Critical' in str(reduced)


def test_historical_events_do_not_change_after_appending():
    call = ModelResponse(parts=[ToolCallPart(tool_name='foreign', args={'q': 'q'}, tool_call_id='c')])
    messages = [call] + [ModelResponse(parts=[ThinkingPart(content='why')]) for _ in range(20)]
    previous = plan(messages).events[0]
    later = plan(messages + [ModelResponse(parts=[TextPart(content='ok')]) for _ in range(60)]).events[0]
    assert previous == later


def test_no_lod_change_between_maintenance_events():
    messages = [ModelResponse(parts=[ToolCallPart(tool_name='foreign', args={'q': 'x' * 9000}, tool_call_id='c')])]
    messages += [ModelRequest(parts=[ToolReturnPart(tool_name='foreign', content='y' * 9000, tool_call_id='c')])]
    messages += [ModelResponse(parts=[TextPart(content='ok')]) for _ in range(25)]
    before = WindowedStrategy().view(messages, ContextBudget(window=None))
    after = WindowedStrategy().view(
        messages + [ModelResponse(parts=[TextPart(content='ok')])], ContextBudget(window=None)
    )
    assert [e.step for e in before.events] == [e.step for e in after.events] == [26]
    assert before.messages[0] == after.messages[0]


def test_windowed_drops_reasoning_before_discarding_a_turn():
    messages = thoughts(1, size=1000) + thoughts(1, run='b', size=1000)
    result = WindowedStrategy(reasoning_max_percent=100).view(messages, ContextBudget(window=400, output_reserve=0))
    assert result.estimated_tokens <= 400  # fits after dropping old-turn reasoning
    # Add an older text-only turn: dropping current reasoning is enough; losing that turn isn't needed.
    old = [
        ModelRequest(parts=[UserPromptPart(content='old ' * 100)], run_id='old'),
        ModelResponse(parts=[TextPart(content='ok')], run_id='old'),
    ]
    result = WindowedStrategy(reasoning_max_percent=100).view(
        old + thoughts(1, run='b', size=1400), ContextBudget(window=400, output_reserve=0)
    )
    assert result.dropped_messages == 0
    assert result.estimated_tokens <= 400


def test_pinned_skill_lod_policy_is_actually_applied():
    messages = [
        ModelResponse(parts=[ToolCallPart(tool_name='load_skill', args={'name': 'demo'}, tool_call_id='s')]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name='load_skill',
                    content={'name': 'demo', 'resources': [{'path': 'x' * 1000}]},
                    tool_call_id='s',
                )
            ]
        ),
    ]
    messages += [ModelResponse(parts=[TextPart(content='ok')]) for _ in range(80)]
    p = plan(messages, pressure=True)
    reduced, _ = apply(messages, p, lookup=policies_from(ALL_POLICIES))
    assert p.level_of('s') is ToolLOD.LOD_3
    assert reduced[1].parts[0].content == ALL_POLICIES['load_skill'].compact_output(
        messages[1].parts[0].content, ToolLOD.LOD_3
    )
