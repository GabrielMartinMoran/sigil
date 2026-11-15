import asyncio
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from src.engine.extensions.manager import ExtensionManager
from src.engine.extensions.registration import register_extensions
from src.engine.tooling.catalog import ToolCatalog


def test_external_tool_reaches_agent_and_declares_compaction(tmp_path: Path) -> None:
    directory = tmp_path / 'tools' / 'greeting'
    directory.mkdir(parents=True)
    (directory / 'tools.py').write_text('''from sigil_tools import tool
@tool
async def greet(name: str) -> str:
    """Greet a person."""
    return f"Hello {name}"
''')
    manager = ExtensionManager(tmp_path, global_dir=tmp_path / 'tools')
    asyncio.run(manager.load())
    catalog = ToolCatalog()
    calls = []

    def model(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            assert any(tool.name == 'greet' for tool in info.function_tools)
            return ModelResponse(parts=[ToolCallPart('greet', {'name': 'Ada'})])
        return ModelResponse(parts=[TextPart('done')])

    agent = Agent(FunctionModel(model))
    register_extensions(agent, manager, catalog=catalog)
    result = agent.run_sync('greet Ada')
    assert result.output == 'done'
    assert 'Hello Ada' in str(calls[-1])
    assert catalog.compactor('greet') is not None


def test_same_extension_registers_on_multiple_agents_with_shared_catalog(tmp_path: Path) -> None:
    directory = tmp_path / 'tools' / 'greeting'
    directory.mkdir(parents=True)
    (directory / 'tools.py').write_text(
        'from sigil_tools import tool\n@tool\nasync def greet() -> str:\n    return "hi"\n'
    )
    manager = ExtensionManager(tmp_path, global_dir=tmp_path / 'tools')
    asyncio.run(manager.load())
    catalog = ToolCatalog()
    first = Agent('test')
    second = Agent('test')
    register_extensions(first, manager, catalog=catalog)
    register_extensions(second, manager, catalog=catalog)
    assert not manager.errors
    assert 'greet' in second._function_toolset.tools


def test_invalid_schema_does_not_pollute_catalog(tmp_path: Path) -> None:
    directory = tmp_path / 'tools' / 'broken'
    directory.mkdir(parents=True)
    (directory / 'tools.py').write_text(
        'from sigil_tools import tool\n@tool\nasync def broken(value: object) -> object:\n    return value\n'
    )
    manager = ExtensionManager(tmp_path, global_dir=tmp_path / 'tools')
    asyncio.run(manager.load())
    catalog = ToolCatalog()

    class RejectingTarget:
        def tool_plain(self, func, **kwargs):
            raise ValueError('invalid schema')

    register_extensions(RejectingTarget(), manager, catalog=catalog)
    assert catalog.get('broken') is None
    assert any('invalid schema' in error for error in manager.errors)


def test_injects_tool_context_without_exposing_it_to_model(tmp_path: Path) -> None:
    from sigil_tools import ToolContext

    directory = tmp_path / 'tools' / 'context'
    directory.mkdir(parents=True)
    (directory / 'tools.py').write_text("""from __future__ import annotations
from sigil_tools import ToolContext, tool
@tool
async def identify(ctx: ToolContext, message: str) -> str:
    return f"{ctx.agent_id}:{ctx.agent_name}:{ctx.session_id}:{ctx.workspace}:{message}"
""")
    manager = ExtensionManager(tmp_path, global_dir=tmp_path / 'tools')
    asyncio.run(manager.load())
    seen = []

    def model(messages, info):
        seen.append(messages)
        if len(seen) == 1:
            schema = next(tool.parameters_json_schema for tool in info.function_tools if tool.name == 'identify')
            assert 'ctx' not in schema['properties']
            return ModelResponse(parts=[ToolCallPart('identify', {'message': 'hello'})])
        return ModelResponse(parts=[TextPart('done')])

    agent = Agent(FunctionModel(model))
    context = ToolContext(workspace=tmp_path, agent_id='child-id', agent_name='scout', session_id='session-id')
    register_extensions(agent, manager, catalog=ToolCatalog(), context_factory=lambda _: context)
    agent.run_sync('identify')
    assert f'child-id:scout:session-id:{tmp_path}:hello' in str(seen[-1])


def test_builtin_replacing_catalog_entry_cannot_be_overridden(tmp_path: Path) -> None:
    directory = tmp_path / 'tools' / 'greeting'
    directory.mkdir(parents=True)
    (directory / 'tools.py').write_text(
        'from sigil_tools import tool\n@tool\nasync def greet() -> str:\n    return "external"\n'
    )
    manager = ExtensionManager(tmp_path, global_dir=tmp_path / 'tools')
    asyncio.run(manager.load())
    catalog = ToolCatalog()
    register_extensions(Agent('test'), manager, catalog=catalog)
    catalog.declare('greet')
    agent = Agent('test')
    register_extensions(agent, manager, catalog=catalog)
    assert 'greet' not in agent._function_toolset.tools
    assert any('conflicts' in error for error in manager.errors)
