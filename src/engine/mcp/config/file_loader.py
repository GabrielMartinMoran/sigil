"""Reading a config file and validating its servers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from src.engine.agents.model_config import AgentModelConfig, parse_agent_models
from src.engine.mcp.config.constants import TOP_LEVEL_KEYS
from src.engine.mcp.config.validation import build_spec
from src.engine.mcp.config_models.server_spec import MCPServerSpec


def load_file(
    path: Path, environ: Mapping[str, str]
) -> tuple[dict[str, MCPServerSpec], dict[str, AgentModelConfig], set[str], list[str]]:
    """Reads a config file. Returns ``(servers, agents, invalid agents, errors)``."""
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except OSError as exc:
        return {}, {}, set(), [f'{path}: cannot read config: {exc}']
    except json.JSONDecodeError as exc:
        return {}, {}, set(), [f'{path}: invalid JSON: {exc}']

    if not isinstance(raw, dict):
        return {}, {}, set(), [f'{path}: the config root must be a JSON object.']

    errors: list[str] = []
    unknown = set(raw) - TOP_LEVEL_KEYS
    if 'mcpServers' in unknown:
        unknown.discard('mcpServers')
        errors.append(f'{path}: `mcpServers` is not supported; use snake_case `mcp_servers`.')
    if unknown:
        supported = ', '.join(sorted(TOP_LEVEL_KEYS))
        errors.append(f'{path}: unknown top-level keys: {", ".join(sorted(unknown))}. Supported: {supported}.')

    agents, invalid_agents, agent_errors = parse_agent_models(raw.get('agents', {}), path)
    errors.extend(agent_errors)

    entries = raw.get('mcp_servers', {})
    if not isinstance(entries, dict):
        errors.append(f'{path}: `mcp_servers` must be a JSON object mapping server names to settings.')
        return {}, agents, invalid_agents, errors

    servers: dict[str, MCPServerSpec] = {}
    for name, entry in entries.items():
        spec, error = build_spec(path, name, entry, environ)
        if error is not None:
            errors.append(error)
        elif spec is not None:
            servers[name] = spec
    return servers, agents, invalid_agents, errors
