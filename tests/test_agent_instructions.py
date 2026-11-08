"""Tests for the agent instruction assembly: blocks and order."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai.models.test import TestModel

from src.engine.agents.agent import create_agent
from src.engine.context.instructions.workspace_instructions import WorkspaceInstructions
from src.engine.context.scope import ScopeTracker
from src.engine.skills.registry import SkillRegistry


def _write_skill(root: Path, name: str, body: str) -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'SKILL.md').write_text(
        f'---\nname: {name}\ndescription: Skill {name}.\n---\n{body}',
        encoding='utf-8',
    )


# The agent ships a real provider, and the environment may point it at a route
# that is unavailable or answers with a shape this SDK version rejects. What is
# under test here is the instruction assembly, so the model is stubbed.
TEST_MODEL = TestModel()


def _instruction_names(agent) -> list[str | None]:
    return [getattr(item, 'name', None) for item in agent._instructions]


def test_agent_registers_workspace_and_loaded_skill_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'AGENTS.md').write_text('# Project rules', encoding='utf-8')
        _write_skill(root, 'demo', '# Demo body')

        registry = SkillRegistry([('project', root)])
        agent = create_agent(
            registry,
            workspace=WorkspaceInstructions(root),
            scope=ScopeTracker(root),
        )

        names = _instruction_names(agent)
        assert 'workspace' in names
        assert 'loaded-skills' in names
        assert 'skills' in names


def _last_request_instructions(result) -> str:
    """The instructions the newest request was sent with.

    `all_messages()` includes the history it was given, and the instructions live on
    the request, not on the response.
    """
    from pydantic_ai.messages import ModelRequest

    requests = [message for message in result.all_messages() if isinstance(message, ModelRequest)]
    return (requests[-1].instructions or '') if requests else ''


def _loaded(name: str, *, outcome: str = 'success', call_id: str = 'call-1') -> list:
    """A history whose last turn loaded ``name``."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

    return [
        ModelResponse(parts=[ToolCallPart(tool_name='load_skill', args={'name': name}, tool_call_id=call_id)]),
        ModelRequest(
            parts=[ToolReturnPart(tool_name='load_skill', content='ok', tool_call_id=call_id, outcome=outcome)]
        ),
    ]


def test_loaded_skill_body_reaches_the_instructions():
    """The block is derived from the history of the run, not from a registry.

    That is what makes a resumed session keep the skills it was following: the
    history came back, so the set came back with it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_skill(root, 'demo', '# Demo body\nFollow these steps.')

        registry = SkillRegistry([('project', root)])
        agent = create_agent(registry)

        # No history: nothing was loaded in this run, so the block is empty.
        with agent.override(model=TEST_MODEL):
            result = asyncio.run(agent.run('hello'))
        assert 'Demo body' not in (result.all_messages()[0].instructions or '')

        # A history that loaded the skill: the body is injected again.
        with agent.override(model=TEST_MODEL):
            result = asyncio.run(agent.run('hello again', message_history=_loaded('demo')))
        instructions = _last_request_instructions(result)
        assert 'Demo body' in instructions
        assert 'Follow these steps.' in instructions


def test_a_failed_load_does_not_reach_the_instructions():
    """`ToolErrorRecovery` returns failures as `ToolReturnPart`s.

    A `load_skill` with an unknown name therefore looks successful to anyone who
    only checks that a return part exists; the filter is `outcome`.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_skill(root, 'demo', '# Demo body')

        registry = SkillRegistry([('project', root)])
        agent = create_agent(registry)

        with agent.override(model=TEST_MODEL):
            result = asyncio.run(agent.run('hello', message_history=_loaded('demo', outcome='failed')))
        assert 'Demo body' not in _last_request_instructions(result)


def test_workspace_block_uses_rewrite_policy():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'AGENTS.md').write_text('# Rules', encoding='utf-8')
        agent = create_agent(
            None,
            workspace=WorkspaceInstructions(root),
            scope=ScopeTracker(root),
        )
        with agent.override(model=TEST_MODEL):
            result = asyncio.run(agent.run('hello'))
        parts = result.all_messages()[0].instructions
        assert isinstance(parts, str)
        assert '# Rules' in parts


def test_agent_without_a_skill_registry_has_no_block():
    agent = create_agent(None)
    assert 'loaded-skills' not in _instruction_names(agent)


def test_loaded_skills_block_is_rewritten_not_appended():
    """Loading a skill is rare and the body is large: duplicating it costs more."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_skill(root, 'demo', '# Demo body')
        registry = SkillRegistry([('project', root)])
        agent = create_agent(registry)

        with agent.override(model=TEST_MODEL):
            result = asyncio.run(agent.run('hello', message_history=_loaded('demo')))
        # `rewrite` leaves the block in the instruction prefix, without deltas.
        assert 'Demo body' in _last_request_instructions(result)
        request = [m for m in result.all_messages() if type(m).__name__ == 'ModelRequest'][-1]
        assert not any(type(part).__name__ == 'InstructionDeltaPart' for part in request.parts)


def test_node_context_is_registered_after_all_other_dynamic_instructions(tmp_path):
    registry = SkillRegistry([('project', tmp_path)])
    agent = create_agent(
        registry, workspace=WorkspaceInstructions(tmp_path), scope=ScopeTracker(tmp_path), node_blocks=lambda _: 'state'
    )
    names = _instruction_names(agent)
    assert names[-1] == 'node-context'
    assert names.index('workspace') < names.index('node-context')
    assert names.index('skills') < names.index('node-context')


def test_workspace_changes_replace_prior_instructions(tmp_path):
    from pydantic_ai.messages import InstructionDeltaPart, ModelRequest

    path = tmp_path / 'AGENTS.md'
    path.write_text('old workspace rule', encoding='utf-8')
    agent = create_agent(workspace=WorkspaceInstructions(tmp_path), scope=ScopeTracker(tmp_path))
    with agent.override(model=TEST_MODEL):
        first = asyncio.run(agent.run('first'))
        path.write_text('new workspace rule', encoding='utf-8')
        second = asyncio.run(agent.run('second', message_history=first.all_messages()))
    requests = [message for message in second.all_messages() if isinstance(message, ModelRequest)]
    assert 'new workspace rule' in (requests[-1].instructions or '')
    assert 'old workspace rule' not in (requests[-1].instructions or '')
    assert not any(isinstance(part, InstructionDeltaPart) for message in requests for part in message.parts)


def test_rewriting_workspace_with_legacy_append_history(tmp_path):
    from pydantic_ai import Agent

    path = tmp_path / 'AGENTS.md'
    path.write_text('old workspace rule', encoding='utf-8')
    workspace = WorkspaceInstructions(tmp_path)
    scope = ScopeTracker(tmp_path)
    old_agent = Agent(TEST_MODEL)

    @old_agent.instructions(name='workspace', on_change='append')
    def old_workspace():
        return workspace.render(scope.scopes())

    first = asyncio.run(old_agent.run('first'))
    path.write_text('intermediate workspace rule', encoding='utf-8')
    second = asyncio.run(old_agent.run('second', message_history=first.all_messages()))
    path.write_text('current workspace rule', encoding='utf-8')
    new_agent = create_agent(workspace=workspace, scope=scope)
    with new_agent.override(model=TEST_MODEL):
        third = asyncio.run(new_agent.run('third', message_history=second.all_messages()))
    assert 'current workspace rule' in _last_request_instructions(third)
    assert 'old workspace rule' not in _last_request_instructions(third)
    assert 'intermediate workspace rule' not in _last_request_instructions(third)
    projected = TEST_MODEL.prepare_messages(third.all_messages())
    parts = [part for message in projected for part in message.parts]
    assert 'intermediate workspace rule' not in str(parts)
    assert 'old workspace rule' not in str(parts)
