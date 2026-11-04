"""Tests del armado de instrucciones del agente: bloques y orden."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

os.environ.setdefault('LLM_MODEL', 'test/model')
os.environ.setdefault('OPENROUTER_API_KEY', 'test-key')


from src.engine.agents.agent import create_agent
from src.engine.context.instructions.workspace_instructions import WorkspaceInstructions
from src.engine.context.scope import ScopeTracker
from src.engine.skills.loaded import LoadedSkills
from src.engine.skills.registry import SkillRegistry


def _write_skill(root: Path, name: str, body: str) -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'SKILL.md').write_text(
        f'---\nname: {name}\ndescription: Skill {name}.\n---\n{body}',
        encoding='utf-8',
    )


def _instruction_names(agent) -> list[str | None]:
    return [getattr(item, 'name', None) for item in agent._instructions]


def test_agent_registers_workspace_and_loaded_skill_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'AGENTS.md').write_text('# Reglas del proyecto', encoding='utf-8')
        _write_skill(root, 'demo', '# Demo body')

        registry = SkillRegistry([('project', root)])
        loaded = LoadedSkills()
        agent = create_agent(
            registry,
            workspace=WorkspaceInstructions(root),
            scope=ScopeTracker(root),
            loaded_skills=loaded,
        )

        names = _instruction_names(agent)
        assert 'workspace' in names
        assert 'loaded-skills' in names
        assert 'skills' in names


def test_loaded_skill_body_reaches_the_instructions():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_skill(root, 'demo', '# Demo body\nFollow these steps.')

        registry = SkillRegistry([('project', root)])
        loaded = LoadedSkills()
        agent = create_agent(registry, loaded_skills=loaded)

        # Antes de cargar, el bloque no aporta nada.
        result = asyncio.run(agent.run('hola'))
        first = result.all_messages()[0]
        assert 'Demo body' not in (first.instructions or '')

        loaded.add('demo')
        result = asyncio.run(agent.run('hola de nuevo'))
        first = result.all_messages()[0]
        assert 'Demo body' in (first.instructions or '')
        assert 'Follow these steps.' in (first.instructions or '')


def test_workspace_block_uses_append_policy():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / 'AGENTS.md').write_text('# Reglas', encoding='utf-8')
        agent = create_agent(
            None,
            workspace=WorkspaceInstructions(root),
            scope=ScopeTracker(root),
        )
        result = asyncio.run(agent.run('hola'))
        parts = result.all_messages()[0].instructions
        assert isinstance(parts, str)
        assert '# Reglas' in parts


def test_agent_without_loaded_skills_has_no_block():
    agent = create_agent(None)
    assert 'loaded-skills' not in _instruction_names(agent)


def test_loaded_skills_block_is_rewritten_not_appended():
    """Cargar un skill es raro y el cuerpo es grande: duplicarlo cuesta más."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_skill(root, 'demo', '# Demo body')
        registry = SkillRegistry([('project', root)])
        loaded = LoadedSkills()
        agent = create_agent(registry, loaded_skills=loaded)

        loaded.add('demo')
        result = asyncio.run(agent.run('hola'))
        request = result.all_messages()[0]
        # `rewrite` deja el bloque en el prefijo de instrucciones, sin deltas.
        assert 'Demo body' in (request.instructions or '')
        assert not any(type(part).__name__ == 'InstructionDeltaPart' for part in request.parts)
