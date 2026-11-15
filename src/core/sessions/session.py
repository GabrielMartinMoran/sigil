"""The Session value used by the session store."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic_ai.messages import ModelMessage

from src.core.agents.tree.agent_tree import AgentTree
from src.core.providers.domain.selection import Selection
from src.core.sessions.titling import first_user_message, shorten
from src.core.storage.executions import (
    root_id_for,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Session:
    """A conversation with its pydantic-ai message history."""

    id: str
    cwd: str
    messages: list[ModelMessage] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    _persisted_at: str | None = field(default=None, repr=False, compare=False)
    _draft: bool = field(default=False, repr=False, compare=False)
    _baseline: str | None = field(default=None, repr=False, compare=False)
    _recovery_session: Session | None = field(default=None, repr=False, compare=False)
    title: str | None = None
    selection: Selection | None = None
    """Provider account, model and reasoning level the conversation runs on.

    It travels with the session on purpose: resuming a conversation on a
    different model would silently change cost and behaviour.
    """

    agent: str = ''
    """Agent profile the conversation runs with, by name.

    It travels with the session for the same reason as the model: the
    instructions a conversation was built under are part of it. An empty string
    means "no choice recorded", and the registry falls back to its default.
    """

    tree: AgentTree | None = None
    """The agents of the session: the root the user talks to, and its subagents.

    It is not derived from `agent`: the tree keeps the runs that happened, and a
    subagent that finished an hour ago is part of the conversation's history even
    though nothing is running now.
    """

    allow_external_files: bool = False
    mcp_overrides: dict[str, bool] = field(default_factory=dict)

    tools_tokens: int = 0
    """What the last request measured of the tool schemas.

    Measured per request and written here so `/load` gets the budget back. `0` means
    "not measured in this session yet", which is the truth for a session written
    before these columns existed: the first request of the resumed conversation fills
    them in.
    """

    instructions_tokens: int = 0
    """What the last request measured of the instruction blocks. See `tools_tokens`."""

    @property
    def root_id(self) -> str:
        """The execution that holds this conversation's transcript.

        Derived from the id, so the store and the tree agree on it before either
        has written a row.
        """
        return root_id_for(self.id)

    def touch(self) -> None:
        self.updated_at = _now()

    @property
    def short_id(self) -> str:
        return self.id[:8]

    @property
    def preview(self) -> str:
        """First user message, useful to list sessions."""
        return first_user_message(self.messages)

    @property
    def display_title(self) -> str:
        """What the session is called in the window and in the listing.

        The generated title when there is one. A session that has not sent a
        message yet, or whose title request failed, falls back to the first
        message cut to the same length: an unnamed session must still be
        recognizable in `/load` instead of reading as a bare id.
        """
        if self.title:
            return self.title
        return shorten(self.preview)
