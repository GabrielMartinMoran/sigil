"""Compaction events: the clock, the plan, the levels of detail and the debug table."""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.engine.context.compaction.clock import reasoning_step_count, step_count, tool_call_steps
from src.engine.context.compaction.coordinator import apply, plan, policies_from
from src.engine.context.compaction.events import POLICY_VERSION, CompactionReason
from src.engine.context.compaction.policy import DEFAULT_POLICY, CompactionPolicy
from src.engine.context.meter.debug import debug_turns
from src.engine.tooling.lod import ToolLOD
from src.engine.tooling.lod_policies import ALL_POLICIES


def _step(text: str = 'ok', *, call: str | None = None) -> list:
    """One model response, optionally with a tool call."""
    parts: list = [ThinkingPart(content='why'), TextPart(content=text)]
    if call:
        parts.append(
            ToolCallPart(tool_name='read_file', args={'path': f'{call}.py', 'content': 'x' * 5_000}, tool_call_id=call)
        )
    return [ModelResponse(parts=parts)]


def _pad(steps: int) -> list:
    """Responses that only advance the clock."""
    return [ModelResponse(parts=[TextPart(content=f'step {index}')]) for index in range(steps)]


def _returned(call: str, content: str = 'y' * 5_000) -> list:
    return [ModelRequest(parts=[ToolReturnPart(tool_name='read_file', content=content, tool_call_id=call)])]


def _history(steps: int, *, turn: str = 'r1') -> list:
    """A turn with ``steps`` model responses, each one returning a big result."""
    messages: list = [ModelRequest(parts=[UserPromptPart(content='go')], run_id=turn)]
    for index in range(steps):
        messages.extend(_step(call=f'c{index}'))
        messages.extend(_returned(f'c{index}'))
    return messages


def test_a_step_is_one_model_response_whatever_its_tool_calls():
    """Three parallel calls are one decision, so they are one step."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content='go')]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name='read_file', args={'path': 'a'}, tool_call_id='a'),
                ToolCallPart(tool_name='read_file', args={'path': 'b'}, tool_call_id='b'),
                ToolCallPart(tool_name='read_file', args={'path': 'c'}, tool_call_id='c'),
            ]
        ),
        ModelRequest(parts=[ToolReturnPart(tool_name='read_file', content='ok', tool_call_id='a')]),
    ]
    assert step_count(messages) == 1
    assert tool_call_steps(messages) == {'a': 1, 'b': 1, 'c': 1}


def test_the_clock_is_derived_from_the_transcript():
    """Resume and rollback need no bookkeeping: the count is the log."""
    messages = _history(3)
    assert step_count(messages) == 3
    # Rollback is a shorter transcript, and therefore a shorter clock.
    assert step_count(messages[:3]) == 1


def test_reasoning_steps_are_a_different_clock_than_steps():
    messages = [
        ModelRequest(parts=[UserPromptPart(content='go')]),
        ModelResponse(parts=[TextPart(content='no reasoning')]),
        ModelResponse(parts=[ThinkingPart(content='thought')]),
    ]
    assert step_count(messages) == 2
    assert reasoning_step_count(messages) == 1


def test_operational_events_land_on_the_scheduled_boundaries():
    plan_result = plan(_history(DEFAULT_POLICY.reasoning_trigger_steps + 2))
    steps = [event.step for event in plan_result.events]
    assert steps == [DEFAULT_POLICY.reasoning_trigger_steps]
    assert all(event.reason is CompactionReason.OPERATIONAL for event in plan_result.events)
    assert plan_result.events[0].policy_version == POLICY_VERSION


def test_a_short_execution_has_no_event():
    assert plan(_history(3)).events == ()


def test_budget_pressure_is_its_own_reason_without_duplicating_a_boundary():
    messages = _history(DEFAULT_POLICY.reasoning_trigger_steps)
    plain = plan(messages)
    pressed = plan(messages, pressure=True)
    assert len(plain.events) == len(pressed.events) == 1
    assert pressed.events[0].reason is CompactionReason.OPERATIONAL


def test_pressure_on_an_unscheduled_step_adds_one_budget_event():
    messages = _history(DEFAULT_POLICY.reasoning_trigger_steps + 1)
    pressed = plan(messages, pressure=True)
    assert [event.reason for event in pressed.events] == [
        CompactionReason.OPERATIONAL,
        CompactionReason.BUDGET,
    ]


def test_detail_follows_age_and_never_goes_back():
    """The fences are 25, 50 and 75 steps: the ladder the design settled on."""
    policy = CompactionPolicy()
    assert policy.lod_for_age(0) is ToolLOD.FULL
    assert policy.lod_for_age(24) is ToolLOD.FULL
    assert policy.lod_for_age(25) is ToolLOD.LOD_1
    assert policy.lod_for_age(49) is ToolLOD.LOD_1
    assert policy.lod_for_age(50) is ToolLOD.LOD_2
    assert policy.lod_for_age(74) is ToolLOD.LOD_2
    assert policy.lod_for_age(75) is ToolLOD.LOD_3
    assert policy.lod_for_age(10_000) is ToolLOD.LOD_3
    ages = [policy.lod_for_age(age) for age in range(120)]
    assert ages == sorted(ages)


def test_the_current_step_keeps_full_detail_and_older_steps_do_not():
    messages = [
        ModelRequest(parts=[UserPromptPart(content='go')]),
        *_step(call='old'),
        *_returned('old'),
        *_step(call='new'),
        *_returned('new'),
    ]
    young = plan(messages, lookup=policies_from(ALL_POLICIES))
    # Two steps is young: the default fences start at 25.
    assert young.level_of('old') is ToolLOD.FULL
    assert young.level_of('new') is ToolLOD.FULL

    # With explicit fences the age does separate them, and only the older one moves.
    aged = plan(messages, policy=CompactionPolicy(lod_ages=(1, 2, 3)), lookup=policies_from(ALL_POLICIES))
    assert aged.level_of('old') is ToolLOD.LOD_1
    assert aged.level_of('new') is ToolLOD.FULL


def test_arguments_and_results_are_both_reduced():
    messages = [
        ModelRequest(parts=[UserPromptPart(content='go')]),
        *_step(call='c0'),
        *_returned('c0'),
        *_step(call='c1'),
        *_returned('c1'),
    ]
    policy = CompactionPolicy(lod_ages=(1, 2, 3))
    reduced, count = apply(
        messages,
        plan(messages, policy=policy, lookup=policies_from(ALL_POLICIES)),
        policy=policy,
        lookup=policies_from(ALL_POLICIES),
    )
    assert count == 2
    calls = [part for message in reduced for part in message.parts if isinstance(part, ToolCallPart)]
    returns = [part for message in reduced for part in message.parts if isinstance(part, ToolReturnPart)]
    assert len(calls[0].args['content']) < 5_000
    assert calls[0].args['path'] == 'c0.py'
    assert len(str(returns[0].content)) < 5_000
    # The original is untouched: the plan is a projection.
    assert len(str(messages[2].parts[0].content)) == 5_000


def test_applying_the_plan_twice_is_the_same_as_applying_it_once():
    messages = _history(4)
    policy = CompactionPolicy(lod_ages=(1, 2, 3))
    lookup = policies_from(ALL_POLICIES)
    once, _ = apply(messages, plan(messages, policy=policy, lookup=lookup), policy=policy, lookup=lookup)
    twice, second = apply(once, plan(once, policy=policy, lookup=lookup), policy=policy, lookup=lookup)
    assert second == 0
    assert twice == once


def test_a_pinned_skill_result_is_reduced_but_reconstruction_args_survive():
    """A loaded skill is instruction: shrinking it takes the task away."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content='go')]),
        ModelResponse(parts=[ToolCallPart(tool_name='load_skill', args={'name': 'demo'}, tool_call_id='s')]),
        ModelRequest(parts=[ToolReturnPart(tool_name='load_skill', content='z' * 30_000, tool_call_id='s')]),
        *_step(call='c1'),
        *_returned('c1'),
    ]
    policy = CompactionPolicy(lod_ages=(0, 1, 2))
    reduced, _ = apply(
        messages,
        plan(messages, policy=policy, lookup=policies_from(ALL_POLICIES)),
        policy=policy,
        lookup=policies_from(ALL_POLICIES),
    )
    skill = next(
        part
        for message in reduced
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == 'load_skill'
    )
    assert skill.content != 'z' * 30_000
    assert reduced[1].parts[0].args == {'name': 'demo'}


def test_an_unknown_tool_gets_the_generic_size_policy():
    messages = [
        ModelRequest(parts=[UserPromptPart(content='go')]),
        ModelResponse(parts=[ToolCallPart(tool_name='mcp_thing', args={'q': 'x' * 9_000}, tool_call_id='m')]),
        ModelRequest(parts=[ToolReturnPart(tool_name='mcp_thing', content='y' * 9_000, tool_call_id='m')]),
        *_step(call='c1'),
    ]
    policy = CompactionPolicy(lod_ages=(0, 1, 2))
    reduced, count = apply(
        messages, plan(messages, policy=policy, lookup=policies_from({})), policy=policy, lookup=policies_from({})
    )
    assert count == 3
    call = next(
        part
        for message in reduced
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_name == 'mcp_thing'
    )
    returned = next(
        part
        for message in reduced
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == 'mcp_thing'
    )
    # Age 1 lands on the second fence, whose generic target is 500 characters.
    import json

    assert len(json.dumps(call.args)) <= 500
    assert len(str(returned.content)) <= 500


def test_every_own_tool_declares_both_sides():
    """The declaration is mandatory, and it is two functions, not one."""
    import dataclasses

    for name, policy in ALL_POLICIES.items():
        assert callable(policy.input), name
        assert callable(policy.output), name
        # A policy that returns the value untouched would be a declaration that says
        # nothing: the sizes would never shrink for that tool.
        value = {'path': 'a', 'content': 'x' * 9_000}
        reduced = policy.compact_output(value, ToolLOD.LOD_3)
        assert reduced != value or name in {'list_agents'}, (name, dataclasses.asdict(policy))


def test_a_preserved_argument_survives_the_reduction():
    policy = ALL_POLICIES['read_file']
    value = {'path': 'src/main.py', 'start_line': 3, 'content': 'z' * 9_000}
    reduced = policy.compact_input(value, ToolLOD.LOD_3)
    assert reduced['path'] == 'src/main.py'


def test_a_failed_command_keeps_its_reason_at_every_level():
    value = {'command': 'false', 'returncode': 1, 'stderr': 'boom ' * 900}
    for level in (ToolLOD.LOD_1, ToolLOD.LOD_2, ToolLOD.LOD_3):
        reduced = ALL_POLICIES['run'].compact_output(value, level)
        item = reduced[0] if isinstance(reduced, list) else reduced
        assert item['returncode'] == 1
        assert 'boom' in item['stderr']


def test_the_debug_table_attributes_events_to_the_turn_that_contains_them():
    messages = _history(DEFAULT_POLICY.reasoning_trigger_steps + 1, turn='r1')
    messages += _history(3, turn='r2')
    events = plan(messages).events
    turns = debug_turns(messages, events)
    assert len(turns) == 2
    assert [event.step for event in turns[0].events] == [DEFAULT_POLICY.reasoning_trigger_steps]
    assert turns[1].events == ()
