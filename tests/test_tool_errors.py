"""Tests de la recuperación de errores de tools."""

import asyncio

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.engine.agents.tool_errors import MAX_ERROR_CHARS, ToolErrorRecovery, describe_tool_error


def _model_function(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    """Primera respuesta: llama a la tool. Segunda: contesta texto."""
    if len(messages) == 1:
        return ModelResponse(parts=[ToolCallPart('boom', {})])
    return ModelResponse(parts=[TextPart('listo')])


def _agent(capabilities) -> Agent:
    agent = Agent(FunctionModel(_model_function), instructions='x', capabilities=capabilities)

    @agent.tool_plain
    def boom() -> str:
        raise ValueError('no encontré el old_string')

    return agent


def test_tool_error_returns_to_the_model_instead_of_aborting_the_turn() -> None:
    async def inner() -> None:
        result = await _agent([ToolErrorRecovery()]).run('hola')

        assert result.output == 'listo'
        returns = [
            part
            for message in result.all_messages()
            for part in getattr(message, 'parts', [])
            if isinstance(part, ToolReturnPart)
        ]
        assert len(returns) == 1
        assert returns[0].outcome == 'failed'
        assert 'no encontré el old_string' in str(returns[0].content)

    asyncio.run(inner())


def test_without_the_capability_the_tool_error_aborts_the_run() -> None:
    async def inner() -> None:
        with pytest.raises(ValueError, match='no encontré el old_string'):
            await _agent([]).run('hola')

    asyncio.run(inner())


def test_describe_tool_error_prefixes_the_exception_type() -> None:
    assert describe_tool_error(ValueError('boom')) == 'ValueError: boom'


def test_describe_tool_error_collapses_whitespace_and_caps_length() -> None:
    described = describe_tool_error(ValueError('linea uno\nlinea dos'))
    assert described == 'ValueError: linea uno linea dos'

    long = describe_tool_error(ValueError('x' * (MAX_ERROR_CHARS * 2)))
    assert len(long) <= MAX_ERROR_CHARS + len('ValueError: ')
    assert long.endswith('…')


def test_describe_tool_error_handles_empty_message() -> None:
    assert describe_tool_error(ValueError()) == 'ValueError: (sin detalle)'
