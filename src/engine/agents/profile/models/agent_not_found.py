"""No profile is installed under that name."""

from __future__ import annotations

from src.engine.agents.profile.models.agent_error import AgentError


class AgentNotFound(AgentError):
    """No profile is installed under that name."""
