"""Sandbox calls traverse the real agent tool-permission hooks."""

import asyncio
import shutil
import sys

import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.core.agents.profile.permissions import parse_permissions
from src.core.agents.profile.profile_source import ProfileSource
from src.core.agents.profile.tool_permissions_capability import ToolPermissionsCapability
from src.core.codemode.dispatch import NestedDispatcher
from src.core.codemode.sandbox import execute_python
from src.core.tooling.resource_kind import ResourceKind
from src.core.tooling.tool_group import ToolGroup
from src.core.tooling.tool_permission import ToolPermission
from src.core.tooling.tool_registrar import ToolRegistrar


@pytest.mark.skipif(sys.platform != 'linux' or not shutil.which('bwrap'), reason='requires Linux bubblewrap')
def test_sandbox_nested_calls_obey_profile_permissions() -> None:
    async def run() -> None:
        class Profile:
            permissions = parse_permissions({'read_file': {'allow': ['allowed/**']}})

        agent = Agent(
            FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart('done')])),
            capabilities=[ToolPermissionsCapability(source=ProfileSource(lambda: Profile(), lambda: ''))],
        )
        registrar = ToolRegistrar(agent)
        called: list[str] = []

        @registrar.tool(permission=ToolPermission(groups=(ToolGroup.READ,), resource='path', kind=ResourceKind.PATH))
        def read_file(path: str) -> str:
            called.append(path)
            return path

        @agent.tool
        async def outer(ctx: RunContext[None]) -> str:
            dispatcher = NestedDispatcher(ctx, frozenset({'read_file'}), max_calls=4)
            code = """\
result = await tools.read_file(path='allowed/note')
print(result)
try:
    await tools.read_file(path='private/note')
except RuntimeError as exc:
    print('PermissionDenied' in str(exc))
"""
            result = await execute_python(code, dispatcher=dispatcher)
            assert result.returncode == 0, result.stderr
            assert result.stdout == 'allowed/note\nTrue\n'
            return 'done'

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if len(messages) == 1:
                return ModelResponse(parts=[ToolCallPart('outer', {})])
            return ModelResponse(parts=[TextPart('done')])

        with agent.override(model=FunctionModel(model)):
            await agent.run('test')
        assert called == ['allowed/note']

    asyncio.run(run())
