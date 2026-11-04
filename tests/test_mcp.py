"""Tests del soporte MCP (sin red ni LLM real).

El test de integración levanta un servidor MCP real por stdio usando el SDK
oficial (``mcp.server.mcpserver``), que ya viene instalado como dependencia del
cliente de FastMCP.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import AbstractToolset

from src.engine.mcp.config_loader import expand_env_vars, load_config
from src.engine.mcp.config_models import MCPConfigError
from src.engine.mcp.manager import MCPManager
from src.engine.mcp.resilient import ResilientToolset
from src.engine.mcp.tool_models import ServerStatus
from src.engine.mcp.tools import register_mcp_tools
from src.ui.mcp_report import format_mcp_status

SERVER_SCRIPT = '''\
from mcp.server.mcpserver import MCPServer

mcp = MCPServer('probe')


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@mcp.resource('probe://hello')
def hello() -> str:
    return 'hi there'


@mcp.prompt()
def greet(name: str) -> str:
    return f'Hello {name}'


if __name__ == '__main__':
    mcp.run()
'''


def write_config(path: Path, servers: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'mcp_servers': servers}), encoding='utf-8')
    return path


def stdio_server(tmp_path: Path, **overrides) -> dict:
    script = tmp_path / 'server.py'
    script.write_text(SERVER_SCRIPT, encoding='utf-8')
    entry = {
        'command': sys.executable,
        'args': [str(script)],
        'keep_alive': False,
        'init_timeout': 30,
    }
    entry.update(overrides)
    return entry


class DummyAgent:
    def __init__(self) -> None:
        self.registered = {}

    def tool_plain(self, func):
        self.registered[func.__name__] = func
        return func


class BrokenToolset(AbstractToolset):
    """Toolset que no puede conectar."""

    @property
    def id(self):
        return 'broken'

    async def __aenter__(self):
        raise RuntimeError('cannot connect')

    async def get_tools(self, ctx):
        return {}

    async def call_tool(self, name, args, ctx, tool):
        raise NotImplementedError


# --- expansión de variables de entorno ---------------------------------------


def test_expand_env_vars_supports_defaults():
    env = {'TOKEN': 'abc'}
    assert expand_env_vars('Bearer ${TOKEN}', env) == 'Bearer abc'
    assert expand_env_vars('${MISSING:-fallback}', env) == 'fallback'
    assert expand_env_vars('${MISSING:-}', env) == ''
    assert expand_env_vars({'a': ['${TOKEN}']}, env) == {'a': ['abc']}


def test_expand_env_vars_raises_without_default():
    with pytest.raises(MCPConfigError, match='MISSING'):
        expand_env_vars('${MISSING}', {})


# --- validación de la configuración ------------------------------------------


def test_config_rejects_camel_case_key(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'mcpServers': {}}), encoding='utf-8')
    config = load_config([path], environ={})
    assert config.servers == {}
    assert any('snake_case' in error for error in config.errors)


def test_config_requires_command_or_url(tmp_path):
    path = write_config(tmp_path / 'config.json', {'x': {'enabled': True}})
    config = load_config([path], environ={})
    assert config.servers == {}
    assert any('command' in error and 'url' in error for error in config.errors)


def test_config_rejects_both_transports(tmp_path):
    path = write_config(
        tmp_path / 'config.json',
        {'x': {'command': 'npx', 'url': 'https://example.com/mcp'}},
    )
    config = load_config([path], environ={})
    assert config.servers == {}
    assert any('not both' in error for error in config.errors)


def test_config_rejects_unknown_key(tmp_path):
    path = write_config(tmp_path / 'config.json', {'x': {'command': 'npx', 'bogus': 1}})
    config = load_config([path], environ={})
    assert config.servers == {}
    assert any('bogus' in error for error in config.errors)


def test_config_rejects_stdio_only_fields_on_http(tmp_path):
    path = write_config(
        tmp_path / 'config.json',
        {'x': {'url': 'https://example.com/mcp', 'cwd': '/tmp'}},
    )
    config = load_config([path], environ={})
    assert config.servers == {}
    assert any('cwd' in error for error in config.errors)


def test_config_rejects_invalid_prefix(tmp_path):
    path = write_config(tmp_path / 'config.json', {'x': {'command': 'npx', 'prefix': 'bad-prefix'}})
    config = load_config([path], environ={})
    assert config.servers == {}
    assert any('prefix' in error for error in config.errors)


def test_one_broken_server_does_not_invalidate_the_rest(tmp_path):
    path = write_config(
        tmp_path / 'config.json',
        {'good': {'command': 'npx'}, 'bad': {'command': 'npx', 'url': 'https://x'}},
    )
    config = load_config([path], environ={})
    assert list(config.servers) == ['good']
    assert len(config.errors) == 1


# --- merge y rutas -----------------------------------------------------------


def test_project_config_overrides_global(tmp_path):
    global_cfg = write_config(tmp_path / 'home' / 'config.json', {'a': {'command': 'global-a'}})
    project_cfg = write_config(tmp_path / 'project' / 'config.json', {'a': {'command': 'project-a'}})

    config = load_config([project_cfg, global_cfg], environ={})
    assert config.servers['a'].config.command == 'project-a'
    assert config.sources == (project_cfg, global_cfg)


def test_servers_are_merged_across_files(tmp_path):
    global_cfg = write_config(tmp_path / 'home' / 'config.json', {'a': {'command': 'a'}})
    project_cfg = write_config(tmp_path / 'project' / 'config.json', {'b': {'command': 'b'}})

    config = load_config([project_cfg, global_cfg], environ={})
    assert sorted(config.servers) == ['a', 'b']


def test_cwd_is_relative_to_the_config_file(tmp_path):
    workdir = tmp_path / 'project' / 'work'
    workdir.mkdir(parents=True)
    path = write_config(tmp_path / 'project' / 'config.json', {'x': {'command': 'npx', 'cwd': 'work'}})

    config = load_config([path], environ={})
    assert config.servers['x'].cwd_path == workdir.resolve()


def test_missing_cwd_is_reported(tmp_path):
    path = write_config(tmp_path / 'config.json', {'x': {'command': 'npx', 'cwd': 'nope'}})
    config = load_config([path], environ={})
    assert config.servers == {}
    assert any('cwd' in error for error in config.errors)


def test_env_vars_are_expanded_in_config(tmp_path):
    path = write_config(
        tmp_path / 'config.json',
        {'x': {'command': 'npx', 'env': {'TOKEN': '${TOKEN}'}}},
    )
    config = load_config([path], environ={'TOKEN': 'secret'})
    assert config.servers['x'].config.env == {'TOKEN': 'secret'}


# --- construcción de toolsets ------------------------------------------------


def test_disabled_server_has_no_toolset(tmp_path):
    path = write_config(tmp_path / 'config.json', {'x': {'command': 'npx', 'enabled': False}})
    manager = MCPManager(load_config([path], environ={}).servers)

    assert len(manager) == 1
    assert manager.toolsets == []
    assert manager.get('x').enabled is False


def test_tool_prefix_defaults_to_server_name(tmp_path):
    path = write_config(tmp_path / 'config.json', {'github': {'command': 'npx'}})
    manager = MCPManager(load_config([path], environ={}).servers)
    assert manager.get('github').spec.tool_prefix == 'github'


def test_empty_prefix_disables_prefixing(tmp_path):
    path = write_config(tmp_path / 'config.json', {'github': {'command': 'npx', 'prefix': ''}})
    manager = MCPManager(load_config([path], environ={}).servers)
    assert manager.get('github').spec.tool_prefix == ''


# --- degradación -------------------------------------------------------------


def test_resilient_toolset_degrades_instead_of_failing_the_run():
    wrapper = ResilientToolset(BrokenToolset())
    agent = Agent(TestModel(custom_output_text='ok'), toolsets=[wrapper])

    result = asyncio.run(agent.run('hola'))

    assert result.output == 'ok'
    assert wrapper.error is not None
    assert 'cannot connect' in wrapper.error


def test_resilient_toolset_reports_the_error():
    seen = []
    wrapper = ResilientToolset(BrokenToolset(), on_error=seen.append)

    asyncio.run(wrapper.__aenter__())

    assert seen and 'cannot connect' in seen[0]
    assert asyncio.run(wrapper.get_tools(None)) == {}


# --- integración con un servidor real ----------------------------------------


def test_stdio_server_end_to_end(tmp_path):
    path = write_config(tmp_path / 'config.json', {'probe': stdio_server(tmp_path)})
    manager = MCPManager(load_config([path], environ={}).servers)
    agent = Agent(TestModel(call_tools=['probe_add']), toolsets=manager.toolsets)

    result = asyncio.run(agent.run('sumá 2 y 3'))

    assert 'probe_add' in result.output
    assert manager.errors == []


def test_describe_connects_and_lists_tools(tmp_path):
    path = write_config(tmp_path / 'config.json', {'probe': stdio_server(tmp_path)})
    manager = MCPManager(load_config([path], environ={}).servers)

    statuses = asyncio.run(manager.describe(connect=True))

    assert [status.name for status in statuses] == ['probe']
    assert statuses[0].tools == ['probe_add']
    assert statuses[0].error is None


def test_meta_tools_expose_resources_and_prompts(tmp_path):
    path = write_config(tmp_path / 'config.json', {'probe': stdio_server(tmp_path)})
    manager = MCPManager(load_config([path], environ={}).servers)
    agent = DummyAgent()
    register_mcp_tools(agent, manager)

    resources = asyncio.run(agent.registered['list_mcp_resources']())
    assert [(item.server, item.uri) for item in resources] == [('probe', 'probe://hello')]

    content = asyncio.run(agent.registered['read_mcp_resource']('probe', 'probe://hello'))
    assert content.content == 'hi there'
    assert content.binary is False

    prompts = asyncio.run(agent.registered['list_mcp_prompts']())
    assert [prompt.name for prompt in prompts] == ['greet']
    assert prompts[0].arguments[0].name == 'name'

    resolved = asyncio.run(agent.registered['get_mcp_prompt']('probe', 'greet', {'name': 'Gaby'}))
    assert resolved.messages[0].content == 'Hello Gaby'


def test_meta_tools_are_not_registered_without_servers():
    agent = DummyAgent()
    register_mcp_tools(agent, MCPManager({}))
    assert agent.registered == {}


def test_unknown_server_raises():
    manager = MCPManager({})
    with pytest.raises(MCPConfigError, match='Unknown MCP server'):
        asyncio.run(manager.list_resources('nope'))


def test_tool_calls_are_logged_with_the_prefix(tmp_path):
    events = []
    path = write_config(tmp_path / 'config.json', {'probe': stdio_server(tmp_path)})
    manager = MCPManager(load_config([path], environ={}).servers, tool_logger=events.append)
    agent = Agent(TestModel(call_tools=['probe_add']), toolsets=manager.toolsets)

    asyncio.run(agent.run('sumá'))

    phases = [(event.tool_name, event.phase) for event in events]
    assert ('probe_add', 'start') in phases
    assert ('probe_add', 'success') in phases


# --- reporte de estado --------------------------------------------------------


def test_format_mcp_status_shows_tools_errors_and_state():
    statuses = [
        ServerStatus(name='a', transport='stdio', enabled=True, prefix='a', tools=['a_x'], source='/cfg'),
        ServerStatus(name='b', transport='http', enabled=True, prefix='b', error='boom', source='/cfg'),
        ServerStatus(name='c', transport='stdio', enabled=False, prefix='c', source='/cfg'),
    ]

    report = format_mcp_status(statuses)

    assert 'MCP servers (3)' in report
    assert 'tools: a_x' in report
    assert 'error: boom' in report
    assert 'disabled' in report


def test_format_mcp_status_handles_no_servers():
    assert 'No MCP servers' in format_mcp_status([])


def test_broken_server_does_not_block_the_others(tmp_path):
    path = write_config(
        tmp_path / 'config.json',
        {
            'probe': stdio_server(tmp_path),
            'roto': {'command': '/nonexistent/binary', 'keep_alive': False},
        },
    )
    manager = MCPManager(load_config([path], environ={}).servers)

    statuses = {status.name: status for status in asyncio.run(manager.describe(connect=True))}

    assert statuses['probe'].tools == ['probe_add']
    assert statuses['probe'].error is None
    assert statuses['roto'].error is not None


def test_agent_runs_with_a_broken_server_configured(tmp_path):
    path = write_config(
        tmp_path / 'config.json',
        {
            'probe': stdio_server(tmp_path),
            'roto': {'command': '/nonexistent/binary', 'keep_alive': False},
        },
    )
    manager = MCPManager(load_config([path], environ={}).servers)
    agent = Agent(TestModel(custom_output_text='ok'), toolsets=manager.toolsets)

    result = asyncio.run(agent.run('hola'))

    assert result.output == 'ok'
