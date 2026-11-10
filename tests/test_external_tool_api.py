from pathlib import Path

import pytest

from sigil_tools import ExtensionContext, ToolContext, tool


def test_tool_marks_only_decorated_async_functions() -> None:
    @tool
    async def greeting(name: str) -> str:
        return name

    assert greeting.__sigil_tool__.name is None
    assert greeting.__sigil_tool__.function is greeting


def test_tool_rejects_synchronous_functions() -> None:
    with pytest.raises(TypeError, match='async'):

        @tool
        def greeting() -> str:
            return 'hello'


def test_contexts_expose_extension_and_agent_details() -> None:
    extension = ExtensionContext(extension_dir=Path('/tools/example'), workspace=Path('/workspace'))
    context = ToolContext(workspace=Path('/workspace'), agent_id='child', agent_name='scout', session_id='session')
    assert extension.extension_dir == Path('/tools/example')
    assert context.agent_id == 'child'


def test_tool_rejects_async_compaction_callbacks() -> None:
    from sigil_tools import ToolCompactor, compact_text

    async def compact(value: object) -> object:
        return value

    with pytest.raises(TypeError, match='compaction'):

        @tool(compaction=ToolCompactor(input=compact, output=compact_text))
        async def sample() -> str:
            return 'result'


def test_tool_rejects_async_presentation_callbacks() -> None:
    async def describe(result: object) -> object:
        return result

    with pytest.raises(TypeError, match='describe'):

        @tool(describe=describe)
        async def sample() -> str:
            return 'result'
