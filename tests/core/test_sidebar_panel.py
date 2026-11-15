"""Tests for the side panel: model, skills, MCP counts and the roadmap."""

from __future__ import annotations

from pathlib import Path

from src.core.mcp.config.loader import load_config
from src.core.mcp.manager.mcp_manager import MCPManager


def test_mcp_counts_ignores_disabled_servers(tmp_path: Path) -> None:
    config = tmp_path / 'config.json'
    config.write_text(
        '{"mcp_servers": {"on": {"command": "npx"}, "off": {"command": "npx", "enabled": false}}}',
        encoding='utf-8',
    )
    manager = MCPManager(load_config([config], environ={}).servers)
    ok, failed = manager.counts()
    assert ok == 1
    assert failed == 0


def test_mcp_counts_reports_broken_servers(tmp_path: Path) -> None:
    config = tmp_path / 'config.json'
    # An invalid transport makes the toolset construction fail.
    config.write_text('{"mcp_servers": {"broken": {"url": "not-a-url"}}}', encoding='utf-8')
    manager = MCPManager(load_config([config], environ={}).servers)
    ok, failed = manager.counts()
    assert ok == 0
    assert failed == 1
