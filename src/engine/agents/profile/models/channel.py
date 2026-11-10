"""Which way messages may travel between an agent and the ones around it.

A subagent has three neighbours, and the default is that only one direction works:

- **From its parent**, always. The parent summoned it and can steer it, the same
  way the user steers a turn from the chat.
- **To its parent**, only if it asked for a bidirectional channel. What it sends
  is a *notice*, not an order: the parent reads it, acts on it or not.
- **To its siblings**, also only in a bidirectional channel, and only the ones it
  names. A sibling message is a notice too.

The policy lives in the frontmatter of the subagent:

```yaml
channel: bidirectional          # short form: it may talk back to its parent

channel:                        # long form: it may talk back, and to these
  mode: bidirectional
  parent: true
  siblings: ['reviewer', 'scout']
```

A profile with no `channel` is unidirectional: the parent talks, the subagent
listens and answers once, at the end. That is the shape that costs nothing and
covers most cases, so it is the one you get for free.

Everything here is a *declaration*, not an enforcement: it is read when the
`send_message` tool decides whether a call is allowed. The tool is where the rule
becomes real, because a prompt that merely says "you may not" is a suggestion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.engine.agents.profile.models.agent_name import is_valid_agent_name
from src.engine.agents.profile.models.agent_parse_error import AgentParseError

_KNOWN_KEYS = {'mode', 'parent', 'siblings'}


class ChannelMode(StrEnum):
    """How much of the conversation between two agents is allowed."""

    UNIDIRECTIONAL = 'unidirectional'
    """The parent talks to the subagent. Nothing goes the other way."""

    BIDIRECTIONAL = 'bidirectional'
    """The subagent can also send notices back, to its parent and its siblings."""


DEFAULT_MODE = ChannelMode.UNIDIRECTIONAL
"""What a profile with no `channel` key gets."""


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


UNIDIRECTIONAL = ChannelPolicy()
"""What a profile that says nothing gets."""

_SHORT_TRUE = frozenset({'true', 'yes', 'on', '1', 'bidirectional'})


def parse_channel(raw: Any) -> ChannelPolicy:
    """Reads the ``channel`` key of a profile.

    Shapes accepted:

    - nothing / ``null``: unidirectional;
    - a string: the mode (``unidirectional`` or ``bidirectional``);
    - a mapping: ``mode`` plus ``parent`` and ``siblings``.

    Raises:
        AgentParseError: When the value cannot be read. A channel that silently
            degrades to unidirectional would leave a subagent with a prompt that
            promises a conversation it cannot have.
    """
    if raw is None:
        return UNIDIRECTIONAL
    if isinstance(raw, str):
        return ChannelPolicy(mode=_mode(raw))
    if not isinstance(raw, dict):
        raise AgentParseError(
            f'`channel` must be a mode or a mapping with `mode`, `parent` and `siblings`; got {type(raw).__name__}.'
        )

    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        names = ', '.join(sorted(str(key) for key in unknown))
        raise AgentParseError(f'Unknown keys in `channel`: {names}. Use `mode`, `parent` and `siblings`.')

    mode = _mode(raw.get('mode', DEFAULT_MODE))
    parent = _boolean(raw.get('parent'), 'channel.parent')
    siblings = _names(raw.get('siblings'))
    if mode is ChannelMode.UNIDIRECTIONAL and (siblings or raw.get('parent') is not None):
        raise AgentParseError(
            '`channel.parent` and `channel.siblings` need `mode: bidirectional`: in a unidirectional '
            'channel nothing travels from the subagent to anyone.'
        )
    return ChannelPolicy(mode=mode, to_parent=parent, to_siblings=siblings)


def _mode(raw: Any) -> ChannelMode:
    text = str(raw).strip().lower()
    try:
        return ChannelMode(text)
    except ValueError:
        known = ', '.join(f'`{mode}`' for mode in ChannelMode)
        raise AgentParseError(f'`channel.mode` must be one of {known}; got {text!r}.') from None


def _boolean(raw: Any, label: str) -> bool:
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in _SHORT_TRUE:
        return True
    if text in {'false', 'no', 'off', '0'}:
        return False
    raise AgentParseError(f'`{label}` must be true or false; got {text!r}.')


def _names(raw: Any) -> tuple[str, ...]:
    """The sibling names, de-duplicated and in the order they were written."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list | tuple):
        raise AgentParseError(f'`channel.siblings` must be a list of agent names; got {type(raw).__name__}.')
    names: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise AgentParseError(f'`channel.siblings` must contain strings; got {type(item).__name__}.')
        name = item.strip().lower()
        if not name:
            raise AgentParseError('`channel.siblings` cannot contain an empty name.')
        if not is_valid_agent_name(name):
            raise AgentParseError(
                f'Invalid agent name in `channel.siblings`: {name!r} '
                '(use letters and digits separated by single hyphens or underscores).'
            )
        if name not in names:
            names.append(name)
    return tuple(names)


__all__ = ['DEFAULT_MODE', 'UNIDIRECTIONAL', 'ChannelMode', 'ChannelPolicy', 'parse_channel']
