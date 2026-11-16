"""Shared agents tree control test support."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import UsageLimits

from src.core.agents.agent import create_agent
from src.core.agents.agent_factory import AgentFactory
from src.core.agents.agent_spec import AgentSpec
from src.core.agents.profile.agent_registry import AgentRegistry
from src.core.agents.profile.profile_source import ProfileSource
from src.core.agents.tree.agent_tree import AgentTree
from src.core.agents.tree.agent_tree_store import AgentTreeStore
from src.core.agents.tree.sigil_deps import SigilDeps
from src.core.agents.tree.subagent_summoner import SubagentSummoner
from src.core.agents.tree.tools import register_tree_tools
from src.core.sessions.session_store import SessionStore

FRONTMATTER = '---\nname: {name}\ndescription: {description}\n{extra}---\n{body}'


def write_profile(root: Path, name: str, *, extra: str = '', body: str = 'Be brief.') -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f'{name}.md'
    path.write_text(
        FRONTMATTER.format(name=name, description=f'Profile {name}.', extra=extra, body=body),
        encoding='utf-8',
    )
    return path


def _model(handler: Callable[[list[ModelMessage], AgentInfo], ModelResponse]) -> FunctionModel:
    return FunctionModel(handler)


def _texts(messages: list[ModelMessage]) -> list[str]:
    return [
        str(part.content)
        for message in messages
        for part in getattr(message, 'parts', ())
        if getattr(part, 'part_kind', '') == 'user-prompt'
    ]


def _tool_returns(messages: list[ModelMessage]) -> list[Any]:
    return [
        part
        for message in messages
        for part in getattr(message, 'parts', ())
        if getattr(part, 'part_kind', '') == 'tool-return'
    ]


@dataclass
class Env:
    """A session, its tree, the summoner, and one model per profile."""

    tmp: Path
    registry: AgentRegistry
    trees: AgentTreeStore
    summoner: SubagentSummoner
    factory: AgentFactory
    scripts: dict[str, Callable[[list[ModelMessage], AgentInfo], ModelResponse]] = field(default_factory=dict)

    def tree(self) -> AgentTree:
        return self.trees.active


def make_env(
    tmp_path: Path,
    *,
    root: Callable[[list[ModelMessage], AgentInfo], ModelResponse],
    profiles: dict[str, dict[str, Any]] | None = None,
    scripts: dict[str, Callable[[list[ModelMessage], AgentInfo], ModelResponse]] | None = None,
    max_steps: int = 0,
    max_depth: int = 3,
    root_run: Callable[[], Any | None] | None = None,
    capabilities: list[Any] | None = None,
    agent_model: Callable[[str], Any | None] | None = None,
) -> Env:
    """Builds the session: ``profiles`` is name -> (frontmatter extra, model)."""
    write_profile(tmp_path, 'wizard')
    registry_profiles: dict[str, dict[str, Any]] = dict(profiles or {})
    for name, spec in registry_profiles.items():
        write_profile(tmp_path, name, extra=spec.get('extra', 'role: subagent\n'), body=spec.get('body', 'You work.'))
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='wizard')
    trees = AgentTreeStore()
    env = Env(tmp=tmp_path, registry=registry, trees=trees, summoner=None, factory=None)  # type: ignore[arg-type]
    models: dict[str, FunctionModel] = {'wizard': _model(root)}
    for name, script in (scripts or {}).items():
        models[name] = _model(script)
    env.scripts = scripts or {}

    def build(profile):
        agent = create_agent(
            None,
            model=models[profile.name],
            agent_registry=registry,
            profile=ProfileSource.fixed(profile),
            extra_capabilities=capabilities,
        )
        register_tree_tools(agent, env.summoner)

        # A tool with a cost, so a scripted subagent can spend a step on something
        # real. It is the cheapest way to exercise a budget end to end.
        @agent.tool_plain(name='ping')
        def ping() -> str:
            return 'pong'

        return agent

    def install(target) -> None:
        register_tree_tools(target, env.summoner)

    env.summoner = SubagentSummoner(
        trees=trees,
        registry=registry,
        builder=lambda: build,
        max_steps=max_steps,
        max_depth=max_depth,
        root_run=root_run,
        agent_model=agent_model,
    )
    env.factory = AgentFactory(
        AgentSpec(
            model=_model(root),
            agent_registry=registry,
            tool_registrars=[install],
            extra_capabilities=capabilities or (),
        )
    )
    session = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions').create_session()
    trees.bind(session, root_name='wizard')
    return env


def run_root(env: Env, prompt: str = 'go') -> list[ModelMessage]:
    """Runs the root turn and returns its history."""

    async def inner():
        agent = env.factory.root()
        async with agent.iter(prompt, deps=SigilDeps(), usage_limits=UsageLimits(request_limit=None)) as run:
            async for _node in run:
                pass
        return run.all_messages()

    return asyncio.run(inner())


class _LiveRun:
    """A run that only records what was enqueued into it."""

    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def enqueue(self, text: str) -> str:
        self.enqueued.append(text)
        return 'id'


def node_role():
    from src.core.agents.profile.models.agent_role import AgentRole

    return AgentRole.SUBAGENT


def _root_calling(tool: str, args: dict[str, Any]) -> Callable[[list[ModelMessage], AgentInfo], ModelResponse]:
    """A root model that calls one tool and then answers."""
    calls = {'n': 0}

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls['n'] += 1
        if calls['n'] == 1:
            return ModelResponse(parts=[ToolCallPart(tool, args)])
        return ModelResponse(parts=[TextPart('root done')])

    return model


def _root_with_script(steps: list[tuple[str, dict[str, Any] | None]]):
    """A root model that walks a list of tool calls, then answers."""
    state = {'i': 0}

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if state['i'] >= len(steps):
            return ModelResponse(parts=[TextPart('root done')])
        tool, args = steps[state['i']]
        state['i'] += 1
        return ModelResponse(parts=[ToolCallPart(tool, args or {})])

    return model
