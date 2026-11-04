"""Tests del registro de tools con logging automático."""

import asyncio
import inspect
from typing import get_type_hints

import pytest

from src.engine.tools.tool_registry import (
    ToolCallEvent,
    ToolLoggerHub,
    ToolRegistrar,
    register_tool,
    wrap_tool,
)


class RecordingLogger:
    def __init__(self) -> None:
        self.events: list[ToolCallEvent] = []

    def __call__(self, event: ToolCallEvent) -> None:
        self.events.append(event)

    @property
    def phases(self) -> list[str]:
        return [event.phase for event in self.events]


class DummyAgent:
    def __init__(self) -> None:
        self.registered: dict[str, object] = {}
        self.options: dict[str, dict[str, object]] = {}

    def _register(self, func, kwargs):
        name = kwargs.get('name') or func.__name__
        self.registered[name] = func
        self.options[name] = kwargs
        return func

    def tool_plain(self, func, **kwargs):
        return self._register(func, kwargs)

    def tool(self, func, **kwargs):
        return self._register(func, kwargs)


# --- Wrapper ------------------------------------------------------------------


def test_sync_tool_logs_start_and_success():
    logger = RecordingLogger()

    def add(a: int, b: int = 2) -> int:
        return a + b

    wrapped = wrap_tool(add, tool_name='add', logger=logger)
    assert wrapped(3) == 5

    assert logger.phases == ['start', 'success']
    assert logger.events[0].args == {'a': 3, 'b': 2}
    assert logger.events[1].duration_ms is not None
    assert logger.events[1].result_preview == '5'


def test_async_tool_logs_start_and_success():
    logger = RecordingLogger()

    async def shout(text: str) -> str:
        return text.upper()

    wrapped = wrap_tool(shout, tool_name='shout', logger=logger)
    assert asyncio.run(wrapped('hi')) == 'HI'

    assert logger.phases == ['start', 'success']
    assert logger.events[0].args == {'text': 'hi'}
    assert logger.events[1].result_preview == 'HI'


def test_error_is_logged_and_reraised():
    logger = RecordingLogger()

    def boom() -> None:
        raise ValueError('nope')

    wrapped = wrap_tool(boom, tool_name='boom', logger=logger)
    with pytest.raises(ValueError, match='nope'):
        wrapped()

    assert logger.phases == ['start', 'error']
    assert 'ValueError: nope' in logger.events[1].error


def test_context_tool_skips_ctx_in_logged_args():
    logger = RecordingLogger()

    def with_ctx(ctx: object, value: int) -> int:
        return value

    wrapped = wrap_tool(with_ctx, tool_name='with_ctx', logger=logger, takes_ctx=True)
    assert wrapped(object(), 7) == 7

    assert logger.events[0].args == {'value': 7}


def test_wrapper_preserves_name_docstring_and_signature():
    def original(query: str, limit: int = 5) -> str:
        """Do something useful."""
        return query * limit

    wrapped = wrap_tool(original, tool_name='original')

    assert wrapped.__name__ == 'original'
    assert wrapped.__doc__ == 'Do something useful.'
    assert inspect.signature(wrapped) == inspect.signature(original)


def test_wrapper_resolves_string_annotations():
    def typed(value):
        return str(value)

    typed.__annotations__ = {'value': 'int', 'return': 'str'}
    wrapped = wrap_tool(typed, tool_name='typed')
    hints = get_type_hints(wrapped, include_extras=True)
    assert hints['value'] is int
    assert hints['return'] is str


def test_logging_failure_does_not_break_tool():
    def broken_logger(event: ToolCallEvent) -> None:
        raise RuntimeError('logger exploded')

    def tool(value: int) -> int:
        return value * 2

    wrapped = wrap_tool(tool, tool_name='tool', logger=broken_logger)
    assert wrapped(4) == 8


def test_sync_tool_with_async_logger_in_event_loop():
    events: list[ToolCallEvent] = []

    async def async_logger(event: ToolCallEvent) -> None:
        events.append(event)

    def tool(value: int) -> int:
        return value

    wrapped = wrap_tool(tool, tool_name='tool', logger=async_logger)

    async def scenario():
        assert wrapped(1) == 1
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert [event.phase for event in events] == ['start', 'success']


# --- Registrar ----------------------------------------------------------------


def test_registrar_registers_with_options():
    agent = DummyAgent()
    registrar = ToolRegistrar(agent)

    @registrar.tool(name='renamed', description='A description')
    def original(value: int) -> int:
        return value

    assert 'renamed' in agent.registered
    assert agent.registered['renamed'] is original
    assert agent.options['renamed'] == {'name': 'renamed', 'description': 'A description'}


def test_register_tool_one_off():
    agent = DummyAgent()

    def plain(value: int) -> int:
        return value

    wrapped = register_tool(agent, plain)
    assert agent.registered['plain'] is wrapped


def test_context_tool_uses_agent_tool():
    agent = DummyAgent()
    registrar = ToolRegistrar(agent)

    @registrar.context_tool
    def contextual(ctx: object, value: int) -> int:
        return value

    assert 'contextual' in agent.registered


# --- Hub ----------------------------------------------------------------------


def test_hub_fans_out_to_sync_and_async_sinks():
    sync_events: list[ToolCallEvent] = []
    async_events: list[ToolCallEvent] = []

    async def async_sink(event: ToolCallEvent) -> None:
        async_events.append(event)

    hub = ToolLoggerHub([sync_events.append, async_sink])

    async def scenario():
        await hub.emit(ToolCallEvent('demo', 'start'))

    asyncio.run(scenario())
    assert len(sync_events) == 1
    assert len(async_events) == 1


def test_hub_remove_sink():
    events: list[ToolCallEvent] = []
    hub = ToolLoggerHub([events.append])
    hub.add_sink(events.append)  # duplicado: no se agrega
    assert len(hub._sinks) == 1
    hub.remove_sink(events.append)
    assert len(hub._sinks) == 0


def test_tool_manager_register_tool(tmp_path):
    from src.engine.skills.registry import SkillRegistry
    from src.engine.tools.tool_manager import ToolManager

    agent = DummyAgent()
    manager = ToolManager(skill_registry=SkillRegistry([('project', tmp_path)]))

    def external(value: int) -> int:
        return value

    manager.register_tool(agent, external)
    assert 'external' in agent.registered


def test_tool_manager_passes_logger_to_all_tools(tmp_path):
    from src.engine.skills.registry import SkillRegistry
    from src.engine.tools.tool_manager import ToolManager

    events: list[ToolCallEvent] = []
    hub = ToolLoggerHub([events.append])
    agent = DummyAgent()
    manager = ToolManager(skill_registry=SkillRegistry([('project', tmp_path)]), tool_logger=hub)
    manager.register_tools(agent)

    asyncio.run(agent.registered['run'](['true']))

    assert [event.phase for event in events] == ['start', 'success']
    assert events[0].tool_name == 'run'
    assert events[0].args['cmds'] == ['true']
