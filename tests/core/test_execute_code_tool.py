"""The code tool is available to agents with a bounded, configurable timeout."""

import asyncio

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.core.codemode.tools import register_code_tools


def test_registered_code_tool_executes_and_returns_output() -> None:
    async def run() -> None:
        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if len(messages) == 1:
                return ModelResponse(
                    parts=[ToolCallPart('execute_code', {'code': "print('ready')", 'timeout_seconds': 10})]
                )
            return ModelResponse(parts=[TextPart('done')])

        agent = Agent(FunctionModel(model))
        register_code_tools(agent)
        result = await agent.run('test')
        returns = [p for m in result.all_messages() for p in m.parts if isinstance(p, ToolReturnPart)]
        assert len(returns) == 1
        assert 'ready' in str(returns[0].content)

    asyncio.run(run())


def test_code_tool_calls_real_file_tool_using_run_permissions(tmp_path, monkeypatch) -> None:
    from src.core.files.tools import register_file_tools

    async def run() -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / 'one.txt').write_text('visible')

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if len(messages) == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            'execute_code',
                            {
                                'code': "result = await tools.read_file(path='one.txt')\nprint(result['content'])",
                                'timeout_seconds': 10,
                            },
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart('done')])

        agent = Agent(FunctionModel(model))
        register_file_tools(agent)
        register_code_tools(agent)
        result = await agent.run('test')
        returns = [p for m in result.all_messages() for p in m.parts if isinstance(p, ToolReturnPart)]
        assert len(returns) == 1
        assert 'visible' in str(returns[0].content)

    asyncio.run(run())


def test_code_tool_does_not_bypass_nested_write_denial(tmp_path, monkeypatch) -> None:
    from src.core.agents.profile.permissions import parse_permissions
    from src.core.agents.profile.profile_source import ProfileSource
    from src.core.agents.profile.tool_permissions_capability import ToolPermissionsCapability
    from src.core.files.tools import register_file_tools

    class Profile:
        permissions = parse_permissions({'write': 'deny'})

    async def run() -> None:
        monkeypatch.chdir(tmp_path)

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if len(messages) == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            'execute_code',
                            {
                                'code': "await tools.write_file(path='blocked.txt', operations=[{'type':'write','content':'x'}])",
                                'timeout_seconds': 10,
                            },
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart('done')])

        agent = Agent(
            FunctionModel(model),
            capabilities=[
                ToolPermissionsCapability(source=ProfileSource(lambda: Profile(), lambda: '')),
            ],
        )
        register_file_tools(agent)
        register_code_tools(agent)
        result = await agent.run('test')
        returns = [p for m in result.all_messages() for p in m.parts if isinstance(p, ToolReturnPart)]
        assert len(returns) == 1
        assert 'denied' in str(returns[0].content).lower() or 'unknown tool' in str(returns[0].content).lower()
        assert not (tmp_path / 'blocked.txt').exists()

    asyncio.run(run())


def test_code_output_marks_nonzero_and_timeout_as_failure() -> None:
    from src.core.tooling.outputs import present_output

    for result in (
        {'returncode': 1, 'stdout': '', 'stderr': 'boom', 'timed_out': False},
        {'returncode': None, 'stdout': '', 'stderr': 'late', 'timed_out': True},
    ):
        shown = present_output('execute_code', result)
        assert shown.summary is not None
        assert shown.summary.status == 'bad'
        assert result['stderr'] in shown.text


def test_code_timeout_schema_is_optional_and_bounded() -> None:
    seen = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(tool.parameters_json_schema for tool in info.function_tools if tool.name == 'execute_code')
        return ModelResponse(parts=[TextPart('done')])

    agent = Agent(FunctionModel(model))
    register_code_tools(agent)
    asyncio.run(agent.run('Inspect tools.'))
    schema = seen[0]
    assert 'timeout_seconds' not in schema.get('required', [])
    timeout = schema['properties']['timeout_seconds']
    assert timeout['default'] == 120
    assert timeout['minimum'] == 1
    assert timeout['maximum'] == 120
