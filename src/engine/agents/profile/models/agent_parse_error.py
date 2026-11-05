"""The profile file does not follow the format."""

from __future__ import annotations

from src.engine.agents.profile.models.agent_error import AgentError


class AgentParseError(AgentError):
    """The profile file does not follow the format."""
