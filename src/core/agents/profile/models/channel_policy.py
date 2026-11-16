"""The ChannelPolicy value of the agents layer."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.agents.profile.models.channel_mode import ChannelMode

DEFAULT_MODE = ChannelMode.UNIDIRECTIONAL


@dataclass(frozen=True, slots=True)
class ChannelPolicy:
    """The channel of one subagent, as its profile declares it."""

    mode: ChannelMode = DEFAULT_MODE
    to_parent: bool = True
    """Whether it may send notices to its direct parent. Bidirectional only."""

    to_siblings: tuple[str, ...] = ()
    """Subagent names it may send notices to. Bidirectional only, and by name."""

    @property
    def bidirectional(self) -> bool:
        return self.mode is ChannelMode.BIDIRECTIONAL

    def may_message_parent(self) -> bool:
        """Whether this subagent may talk back to whoever summoned it."""
        return self.bidirectional and self.to_parent

    def may_message_sibling(self, name: str) -> bool:
        """Whether this subagent may talk to the sibling called ``name``."""
        return self.bidirectional and name in self.to_siblings

    def describe(self) -> str:
        """One line for the picker or a log."""
        if not self.bidirectional:
            return 'parent → agent'
        targets = []
        if self.to_parent:
            targets.append('parent')
        if self.to_siblings:
            targets.append(', '.join(self.to_siblings))
        return f'parent ⇄ agent ({", ".join(targets) or "nothing"})'
