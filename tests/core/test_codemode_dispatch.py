"""Nested calls use the agent's validated, permission-aware tool manager."""

import asyncio

import pytest
from pydantic import ValidationError
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.core.agents.profile.permissions import parse_permissions
from src.core.agents.profile.profile_source import ProfileSource
from src.core.agents.profile.tool_permissions_capability import ToolPermissionsCapability
from src.core.codemode.dispatch import NestedDispatcher
from src.core.tooling.resource_kind import ResourceKind
from src.core.tooling.tool_group import ToolGroup
from src.core.tooling.tool_permission import ToolPermission
from src.core.tooling.tool_registrar import ToolRegistrar


class Profile:
    permissions = parse_permissions({'read_file': {'allow': ['allowed/**']}})


def test_nested_dispatch_applies_validation_and_profile_permissions() -> None:
    async def run() -> None:
        profile = Profile()
        agent = Agent(
            FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart('done')])),
            capabilities=[ToolPermissionsCapability(source=ProfileSource(lambda: profile, lambda: ''))],
        )
        tools = ToolRegistrar(agent)
        called: list[str] = []

        @tools.tool(permission=ToolPermission(groups=(ToolGroup.READ,), resource='path', kind=ResourceKind.PATH))
        def read_file(path: str) -> str:
            called.append(path)
            return path

        @agent.tool
        async def outer(ctx: RunContext[None]) -> str:
            dispatcher = NestedDispatcher(ctx, frozenset({'read_file'}), max_calls=3)
            with pytest.raises(ValueError, match='not available'):
                await dispatcher.call('shell', {'cmds': ['pwd']})
            assert await dispatcher.call('read_file', {'path': 'allowed/a'}) == 'allowed/a'
            with pytest.raises(ToolFailed, match='PermissionDenied'):
                await dispatcher.call('read_file', {'path': 'private/a'})
            with pytest.raises(ValidationError):
                await dispatcher.call('read_file', {'path': 123})
            with pytest.raises(ValueError, match='limit'):
                await dispatcher.call('read_file', {'path': 'allowed/a'})
            return 'done'

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if len(messages) == 1:
                return ModelResponse(parts=[ToolCallPart('outer', {})])
            return ModelResponse(parts=[TextPart('done')])

        with agent.override(model=FunctionModel(model)):
            await agent.run('test')
        assert called == ['allowed/a']

    asyncio.run(run())


def test_nested_writes_are_serialized() -> None:
    async def run() -> None:
        agent = Agent(FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart('done')])))
        active = 0
        peak = 0
        release = asyncio.Event()
        first_started = asyncio.Event()

        @agent.tool_plain
        async def write_file(path: str) -> str:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            first_started.set()
            await release.wait()
            active -= 1
            return path

        @agent.tool
        async def outer(ctx: RunContext[None]) -> str:
            dispatcher = NestedDispatcher(ctx, frozenset({'write_file'}), max_calls=2)
            first = asyncio.create_task(dispatcher.call('write_file', {'path': 'a'}))
            await first_started.wait()
            assert dispatcher._write_lock.locked()
            second = asyncio.create_task(dispatcher.call('write_file', {'path': 'a'}))
            release.set()
            await asyncio.gather(first, second)
            return 'done'

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return ModelResponse(parts=[ToolCallPart('outer', {})] if len(messages) == 1 else [TextPart('done')])

        with agent.override(model=FunctionModel(model)):
            await agent.run('test')
        assert peak == 1

    asyncio.run(run())
