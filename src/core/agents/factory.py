"""Building the agent object of a node, from everything the session already has.

The composition root assembles the session once: the model, the tools, the MCP
toolsets, the capabilities that project the context and gate the model features,
the workspace instructions. Every agent in the tree wants the same collection —
a subagent is an agent, so it has the same tools and permissions mechanism.
Each run can override the shared model, and each profile defines its behavior.

That is what this factory is: the shared half, held once, plus the per-profile
half, built on demand. It exists instead of a second assembly in the composition
root because the registrars are closures over live objects (the question hub, the
roadmap store, the MCP gateway) and a copy of that wiring in a second place would
drift the first time a tool is added.

Agents are cached per profile and reused. A run is per call — ``agent.iter()``
opens its own — so one agent object per profile is enough, and it is the same
reason the root agent is built once and reused across turns.
"""

from __future__ import annotations

from src.core.agents.agent_spec import AgentSpec

"""Registers one family of tools on an agent. ``register_file_tools`` and friends."""


__all__ = ['AgentSpec']
