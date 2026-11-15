import asyncio
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from src.engine.extensions.toolset import ExtensionToolset
from src.engine.tooling.catalog import ToolCatalog


def test_lazy_load_and_execute_project_tool_after_authorization(tmp_path: Path) -> None:
    directory = tmp_path / '.sigil' / 'tools' / 'example'
    directory.mkdir(parents=True)
    (directory / 'tools.py').write_text(
        'from sigil_tools import tool\n@tool\nasync def greet() -> str:\n    return "hello"\n'
    )
    requests = []

    async def authorize(path: Path) -> bool:
        requests.append(path)
        return True

    toolset = ExtensionToolset(
        workspace=lambda: tmp_path, catalog=ToolCatalog(), authorize=authorize, global_dir=tmp_path / 'global'
    )
    messages_seen = []

    def model(messages, info):
        messages_seen.append(messages)
        if len(messages_seen) == 1:
            assert any(item.name == 'greet' for item in info.function_tools)
            return ModelResponse(parts=[ToolCallPart('greet', {})])
        return ModelResponse(parts=[TextPart('done')])

    async def run() -> None:
        agent = Agent(FunctionModel(model), toolsets=[toolset])
        result = await agent.run('hello')
        assert result.output == 'done'
        assert 'hello' in str(messages_seen[-1])
        await toolset.close()

    asyncio.run(run())
    assert requests == [tmp_path]


def test_workspace_change_unloads_previous_tools_and_requests_new_trust(tmp_path: Path) -> None:
    current = [tmp_path / 'one']
    marker = tmp_path / 'closed'
    for workspace, name in ((current[0], 'first_tool'), (tmp_path / 'two', 'second_tool')):
        directory = workspace / '.sigil' / 'tools' / 'example'
        directory.mkdir(parents=True)
        (directory / 'tools.py').write_text(f"""from pathlib import Path
from sigil_tools import tool
async def on_unload(ctx):
    Path({str(marker)!r}).touch()
@tool
async def {name}() -> str:
    return 'ok'
""")
    requests = []

    async def authorize(path: Path) -> bool:
        requests.append(path)
        return True

    catalog = ToolCatalog()
    toolset = ExtensionToolset(
        workspace=lambda: current[0], catalog=catalog, authorize=authorize, global_dir=tmp_path / 'global'
    )
    advertised = []

    def model(messages, info):
        advertised.append({tool.name for tool in info.function_tools})
        return ModelResponse(parts=[TextPart('done')])

    async def run() -> None:
        agent = Agent(FunctionModel(model), toolsets=[toolset])
        await agent.run('one')
        current[0] = tmp_path / 'two'
        await agent.run('two')
        assert marker.exists()
        assert advertised == [{'first_tool'}, {'second_tool'}]
        assert catalog.get('first_tool') is None
        assert catalog.get('second_tool') is not None
        await toolset.close()

    asyncio.run(run())
    assert requests == [tmp_path / 'one', tmp_path / 'two']


def test_unload_waits_for_active_invocation(tmp_path: Path) -> None:
    async def run() -> None:
        directory = tmp_path / 'global' / 'example'
        directory.mkdir(parents=True)
        (directory / 'tools.py').write_text("""import asyncio
from sigil_tools import tool
started = asyncio.Event()
release = asyncio.Event()
unloaded = False
async def on_unload(ctx):
    global unloaded
    unloaded = True
@tool
async def wait_for_release() -> str:
    started.set()
    await release.wait()
    assert not unloaded
    return 'ok'
""")

        async def authorize(path: Path) -> bool:
            return False

        toolset = ExtensionToolset(
            workspace=lambda: tmp_path, catalog=ToolCatalog(), authorize=authorize, global_dir=tmp_path / 'global'
        )
        await toolset._load()
        module = toolset._manager._loaded[0].module
        calls = []

        def model(messages, info):
            calls.append(messages)
            if len(calls) == 1:
                return ModelResponse(parts=[ToolCallPart('wait_for_release', {})])
            return ModelResponse(parts=[TextPart('done')])

        agent = Agent(FunctionModel(model), toolsets=[toolset])
        task = asyncio.create_task(agent.run('wait'))
        await asyncio.wait_for(module.started.wait(), timeout=2)
        closing = asyncio.create_task(toolset.close())
        module.release.set()
        await asyncio.wait_for(asyncio.gather(task, closing), timeout=2)
        assert module.unloaded

    asyncio.run(run())


def test_advanced_declarations_reach_logging_compaction_and_presentation(tmp_path: Path) -> None:
    from sigil_tools import ToolSummary
    from src.engine.tooling.outputs import present_output

    directory = tmp_path / 'global' / 'advanced'
    directory.mkdir(parents=True)
    (directory / 'tools.py').write_text("""from sigil_tools import (
    tool, ToolCompactor, ToolPermission, ToolOutput, ToolSummary, compact_text,
)
guard_calls = []
def guard(ctx):
    guard_calls.append(ctx.tool_name)
    return None
def describe(args, result):
    return ToolSummary('custom summary', 'good')
def output(result):
    return ToolOutput('custom human output')
def compact(result):
    return 'compact'
@tool(
    permission=ToolPermission(groups=('read',)),
    concurrency=guard,
    describe=describe,
    output=output,
    compaction=ToolCompactor(input=compact_text, output=compact),
)
async def advanced() -> str:
    return 'original result' * 100
""")
    events = []
    catalog = ToolCatalog()

    async def authorize(path: Path) -> bool:
        return False

    toolset = ExtensionToolset(
        workspace=lambda: tmp_path,
        catalog=catalog,
        authorize=authorize,
        global_dir=tmp_path / 'global',
        logger=events.append,
    )
    calls = []

    def model(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            return ModelResponse(parts=[ToolCallPart('advanced', {})])
        return ModelResponse(parts=[TextPart('done')])

    async def run() -> None:
        await Agent(FunctionModel(model), toolsets=[toolset]).run('use advanced')
        success = next(event for event in events if event.phase == 'success')
        assert success.output == 'custom human output'
        assert success.summary == ToolSummary('custom summary', 'good')
        assert toolset._manager._loaded[0].module.guard_calls == ['advanced']
        assert catalog.get('advanced').permission.groups == ('read',)
        assert catalog.compactor('advanced').compact_output('original result' * 100) == 'compact'
        await toolset.close()
        assert present_output('advanced', 'raw').text != 'custom human output'

    asyncio.run(run())
