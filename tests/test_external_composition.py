"""Local extensions use the real composition, permissions and runtime context."""

import asyncio
from pathlib import Path

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from src.engine.bootstrap import Composition
from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.providers.bootstrap import ProviderPaths
from src.engine.settings import load_settings


def test_composition_loads_project_tools_and_injects_root_context(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    workspace = tmp_path / 'workspace'
    directory = workspace / '.sigil' / 'tools' / 'identity'
    directory.mkdir(parents=True)
    (directory / 'tools.py').write_text("""from sigil_tools import tool, ToolContext
@tool
async def identity(ctx: ToolContext) -> str:
    return f'{ctx.agent_id}:{ctx.agent_name}:{ctx.session_id}:{ctx.workspace}'
""")
    seen = []

    def model(messages, info):
        seen.append(messages)
        if len(seen) == 1:
            assert 'identity' in [tool.name for tool in info.function_tools]
            return ModelResponse(parts=[ToolCallPart('identity', {})])
        return ModelResponse(parts=[TextPart('done')])

    composition = Composition(
        workspace,
        settings=load_settings(),
        model=FunctionModel(model),
        provider_paths=ProviderPaths(tmp_path / 'providers'),
    )
    composition.meter.set_window(100_000)
    questions = []

    async def authorize(items):
        questions.extend(items)
        return [QuestionAnswer(question=items[0].question, answer='Load extensions')]

    composition.question_hub.set_asker(authorize)

    async def run() -> None:
        await composition.agent.run('identify')
        root = composition.agent_trees.active.root
        assert f'{root.id}:{root.name}:{composition.session.id}:{workspace}' in str(seen[-1])
        assert len(questions) == 1
        composition.rebuild_agent()
        assert composition.tool_catalog.compactor('identity') is not None
        await composition.extensions.close()
        assert composition.tool_catalog.get('identity') is None

    asyncio.run(run())


def test_subagent_permissions_filter_external_tools_and_context_identifies_child(tmp_path: Path, monkeypatch) -> None:
    from src.engine.agents.profile.models.agent_profile import AgentProfile
    from src.engine.agents.profile.permissions import parse_permissions
    from src.engine.agents.tree.summoner import SigilDeps

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    directory = tmp_path / 'home' / 'tools' / 'identity'
    directory.mkdir(parents=True)
    (directory / 'tools.py').write_text("""from sigil_tools import tool, ToolContext, ToolPermission
@tool(permission=ToolPermission(groups=('write',)))
async def mutate() -> str:
    return 'changed'
@tool
async def identity(ctx: ToolContext) -> str:
    return ctx.agent_id + ':' + ctx.agent_name
""")
    seen = []

    def model(messages, info):
        seen.append(messages)
        assert 'mutate' not in [tool.name for tool in info.function_tools]
        assert 'identity' in [tool.name for tool in info.function_tools]
        if len(seen) == 1:
            return ModelResponse(parts=[ToolCallPart('identity', {})])
        return ModelResponse(parts=[TextPart('done')])

    composition = Composition(
        tmp_path,
        settings=load_settings(),
        model=FunctionModel(model),
        provider_paths=ProviderPaths(tmp_path / 'providers'),
    )
    composition.meter.set_window(100_000)
    tree = composition.agent_trees.active
    profile = AgentProfile(
        name='restricted',
        description='Read only',
        path=tmp_path,
        profile_file=tmp_path / 'restricted.md',
        source='project',
        permissions=parse_permissions({'write': 'deny'}),
    )
    child = tree.add(name='restricted', task='identify', parent_id=tree.root_id, role=profile.role)
    agent = composition.factory.for_profile(profile)

    async def run() -> None:
        await agent.run('identify', deps=SigilDeps(node_id=child.id))
        assert child.id + ':restricted' in str(seen[-1])
        await composition.extensions.close()

    asyncio.run(run())


def test_server_lifespan_unloads_extensions(tmp_path: Path, monkeypatch) -> None:
    from pydantic_ai.models.test import TestModel

    from src.server.app import create_app
    from src.server.config import ServerConfig
    from src.server.events import EventHub
    from src.server.runtime import ServerRuntime

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    directory = tmp_path / 'home' / 'tools' / 'lifecycle'
    directory.mkdir(parents=True)
    marker = tmp_path / 'unloaded'
    (directory / 'tools.py').write_text(f"""from pathlib import Path
async def on_unload(ctx):
    Path({str(marker)!r}).touch()
""")
    composition = Composition(
        tmp_path,
        settings=load_settings(),
        model=TestModel(call_tools=[]),
        provider_paths=ProviderPaths(tmp_path / 'providers'),
    )
    runtime = ServerRuntime(composition, EventHub())
    app = create_app(runtime, ServerConfig(password='test-password'), dist=tmp_path / 'missing')

    async def run() -> None:
        async with app.router.lifespan_context(app):
            await composition.extensions._load()
            assert not marker.exists()
        assert marker.exists()

    asyncio.run(run())
