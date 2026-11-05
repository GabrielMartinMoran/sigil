"""Where the profile in effect comes from, for one agent object.

An agent reads its profile twice per request: the body, which becomes an
instruction block, and the metadata, which decides which tools exist. There are
two places that can answer, and the difference matters:

- **The registry.** The agent at the root of the session asks the registry what
  is active on every request, so `/agent` changes the behaviour and the tool set
  without rebuilding anything.
- **One fixed profile.** A subagent is pinned to the profile that summoned it for
  its whole run. Its body is still re-read from disk on every request: a profile
  the user is editing is expected to be live, and it costs one small read.

Both cases are the same object here, so `create_agent` and the permission
capability do not need to know which one they were handed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.engine.agents.profile.parser import read_body

if TYPE_CHECKING:
    from src.engine.agents.profile.models.agent_profile import AgentProfile
    from src.engine.agents.profile.registry import AgentRegistry


@dataclass(frozen=True, slots=True)
class ProfileSource:
    """The profile of one agent, and the body that goes with it."""

    profile_of: Callable[[], AgentProfile | None]
    body_of: Callable[[], str]

    def profile(self) -> AgentProfile | None:
        """The metadata in effect right now, or ``None`` when there is none."""
        return self.profile_of()

    def body(self) -> str:
        """The Markdown body in effect right now, or ``''``."""
        return self.body_of()

    @classmethod
    def from_registry(cls, registry: AgentRegistry) -> ProfileSource:
        """The root case: whatever `/agent` says is active, read per request."""
        return cls(profile_of=lambda: registry.active, body_of=registry.active_body)

    @classmethod
    def fixed(cls, profile: AgentProfile) -> ProfileSource:
        """A subagent's case: one profile for the whole run of that node."""
        return cls(profile_of=lambda: profile, body_of=lambda: read_body(profile))


__all__ = ['ProfileSource']
