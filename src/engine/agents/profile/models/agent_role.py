"""What a profile is for: the agent the user talks to, or one summoned by it.

A session has one tree of agents. The **agent** at its root is the one the user
picks — from `/agent`, from `ctrl+a` — and the one the UI shows. The
**subagents** hang under it: they are summoned by a tool, they run a task and
they answer to whoever summoned them, so they are not something the user
chooses. That is the whole distinction, and it is why the picker lists only the
first kind.

The role is declared in the frontmatter of the profile:

```markdown
---
name: researcher
description: Reads a tree and reports what it found.
role: subagent
---
```

``agent`` is the default, so every profile written before this existed keeps
being what it was: an agent the user can select.
"""

from __future__ import annotations

from enum import StrEnum

DEFAULT_ROLE = 'agent'
"""What a profile is when the frontmatter says nothing: an agent."""


class AgentRole(StrEnum):
    """The two kinds of profile a session can use."""

    AGENT = 'agent'
    """The root of the session tree: the one the user interacts with."""

    SUBAGENT = 'subagent'
    """Runs a task for the agent that summoned it, and is hidden from the picker."""

    @property
    def selectable(self) -> bool:
        """Whether the picker may offer this profile as the session's agent."""
        return self is AgentRole.AGENT


__all__ = ['DEFAULT_ROLE', 'AgentRole']
