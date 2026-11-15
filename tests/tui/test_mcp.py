"""Tests for the MCP support (no network and no real LLM).

The integration test brings up a real MCP server over stdio using the official
SDK (``mcp.server.mcpserver``), which already ships as a dependency of the
FastMCP client.
"""

from src.core.mcp.tool_models.server_status import ServerStatus
from src.ui.mcp_report import format_mcp_status

# --- state report -----------------------------------------------------------


def test_format_mcp_status_shows_tools_errors_and_state() -> None:
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


def test_format_mcp_status_handles_no_servers() -> None:
    assert 'No MCP servers' in format_mcp_status([])
