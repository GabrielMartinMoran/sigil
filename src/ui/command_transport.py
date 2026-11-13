"""Which commands this terminal can serve, and where the rest have to go.

The catalogue in :mod:`src.engine.commands` answers *who may serve a command*: the
core, or the client in front of the user. It cannot answer *whether this terminal
can reach that server*, because that depends on which routes the client has been
wired to — a fact about the client, and one that changes cut by cut.

So the catalogue stays the vocabulary and this table is the wiring. Every command
keeps exactly four answers here:

- ``client`` — it configures this window, so it runs here and never travels.
- ``delegated`` — the service owns the effect and this terminal has a sender for
  it: the command travels and paints the service's own answer.
- ``not_wired`` — the service owns the effect and the route exists, but this
  terminal has no sender for it *yet*. The command says so instead of failing
  somewhere the user cannot read.
- ``blocked`` — the service owns the effect and has no endpoint for it at all, so
  there is nothing to wire to.

One table, read once, at the choke point in ``handle_command``. The alternative —
a name list in the dispatcher, another in the prompt, another in a docstring — is
how the previous version ended up refusing `/theme` along with `/rollback`.

The rows are checked against the catalogue when the module is imported. A command
added to one and not the other raises here, at import, instead of turning into a
menu entry that silently does nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from src.engine.commands import COMMANDS, CommandOwner


class TransportState(StrEnum):
    """How this terminal can serve one command."""

    CLIENT = 'client'
    """It runs here: it paints or configures this window and travels nowhere."""

    DELEGATED = 'delegated'
    """The service owns it and this terminal has a sender for its route."""

    NOT_WIRED = 'not_wired'
    """The service owns it and has the route; this terminal has no sender yet."""

    BLOCKED = 'blocked'
    """The service owns it and has no endpoint for it, so nothing can be wired."""


@dataclass(frozen=True)
class Transport:
    """One command's way out of this terminal.

    ``reason`` explains a row that does not run here, in the words the user reads:
    it names what is missing, not which cut is missing it. It is empty exactly for
    the rows that run here, because they have nothing to explain.
    """

    name: str
    owner: CommandOwner
    state: TransportState
    route: str | None = None
    reason: str = ''


_ROWS: tuple[Transport, ...] = (
    # --- the core serves these; this terminal has no sender for them yet ------
    Transport(
        name='/new',
        owner=CommandOwner.CORE,
        state=TransportState.DELEGATED,
        route='/api/session/new',
        reason='the service creates and owns the session row.',
    ),
    Transport(
        name='/load',
        owner=CommandOwner.CORE,
        state=TransportState.DELEGATED,
        route='/api/session/open',
        reason='the service lists and opens its own conversations.',
    ),
    Transport(
        name='/agent',
        owner=CommandOwner.CORE,
        state=TransportState.DELEGATED,
        route='/api/agent',
        reason='the service decides which profile the session runs under.',
    ),
    # Blocked, and not "unconfigured": an account endpoint is what is missing, and
    # telling a user to add a provider they already have would be a wrong answer.
    Transport(
        name='/provider',
        owner=CommandOwner.CORE,
        state=TransportState.DELEGATED,
        route='/api/accounts',
        reason='the service owns the accounts and their credentials.',
    ),
    Transport(
        name='/model',
        owner=CommandOwner.CORE,
        state=TransportState.DELEGATED,
        route='/api/model',
        reason='the service owns the accounts, the catalogues and the switch.',
    ),
    Transport(
        name='/effort',
        owner=CommandOwner.CORE,
        state=TransportState.DELEGATED,
        route='/api/effort',
        reason='the service owns the model and the levels it accepts.',
    ),
    # The defaults are files the service reads and writes, and no route projects
    # them yet. `blocked` and not `not_wired`: there is nothing to point a sender
    # at, so the command says so instead of failing somewhere the user cannot read.
    Transport(
        name='/default-models',
        owner=CommandOwner.CORE,
        state=TransportState.BLOCKED,
        reason='the service owns the defaults files, and no endpoint exposes them yet.',
    ),
    Transport(
        name='/external-files',
        owner=CommandOwner.CORE,
        state=TransportState.DELEGATED,
        route='/api/external-files',
        reason='the service stores the boundary on the session row.',
    ),
    Transport(
        name='/mcp',
        owner=CommandOwner.CORE,
        state=TransportState.DELEGATED,
        route='/api/mcp',
        reason='the service holds the servers and the session overrides.',
    ),
    Transport(
        name='/context',
        owner=CommandOwner.CORE,
        state=TransportState.DELEGATED,
        route='/api/context',
        reason='the service measures the session and prices it.',
    ),
    Transport(
        name='/rollback',
        owner=CommandOwner.CORE,
        state=TransportState.DELEGATED,
        route='/api/rollback',
        reason='the service owns the points, the truncation and the fork.',
    ),
    # --- this window serves these, and they never leave it --------------------
    Transport('/reasoning-visibility', CommandOwner.CLIENT, TransportState.CLIENT),
    Transport('/tools', CommandOwner.CLIENT, TransportState.CLIENT),
    Transport('/theme', CommandOwner.CLIENT, TransportState.CLIENT),
    Transport('/sidebar', CommandOwner.CLIENT, TransportState.CLIENT),
    Transport('/alerts', CommandOwner.CLIENT, TransportState.CLIENT),
    Transport('/debug', CommandOwner.CLIENT, TransportState.CLIENT),
    Transport('/help', CommandOwner.CLIENT, TransportState.CLIENT),
    Transport('/quit', CommandOwner.CLIENT, TransportState.CLIENT),
)


def _covering(rows: tuple[Transport, ...]) -> tuple[Transport, ...]:
    """The rows, proven to cover the catalogue exactly once and to agree with it.

    Import-time on purpose: a missing row is a bug in this file, not something a
    user should discover by typing a command that does nothing. The same invariant
    is asserted in `tests/test_commands_ownership.py`; this is the version that
    fails before the app is built.
    """
    declared = {command.name: command.owner for command in COMMANDS}
    listed = {row.name: row for row in rows}
    if len(listed) != len(rows):
        # Two rows for one command: the later one would win silently, and the
        # table would say two different things about the same command.
        repeated = sorted({row.name for row in rows if [item.name for item in rows].count(row.name) > 1})
        raise ValueError(f'the transport table lists {repeated} more than once')
    missing = sorted(set(declared) - set(listed))
    extra = sorted(set(listed) - set(declared))
    if missing or extra:
        raise ValueError(
            'the transport table must cover the command catalogue exactly: '
            f'missing {missing or "none"}, extra {extra or "none"}'
        )
    for row in rows:
        if row.owner is not declared[row.name]:
            raise ValueError(
                f'{row.name} is {row.owner} in the transport table and {declared[row.name]} in the catalogue'
            )
    return rows


TRANSPORT: tuple[Transport, ...] = _covering(_ROWS)
"""Every command this terminal knows, and how it can serve each one."""

_BY_NAME: dict[str, Transport] = {row.name: row for row in TRANSPORT}


def transport_for(name: str) -> Transport | None:
    """The row for ``name``, or ``None`` when the catalogue never declared it."""
    return _BY_NAME.get(name)


def unavailable_notice(row: Transport) -> str:
    """The one line a command that cannot run here says.

    It names the command, says who serves it, and says what is missing — in that
    order, because that is the order the reader asks. A refused command opens no
    panel and writes nothing; this sentence is the whole answer.
    """
    if row.state is TransportState.NOT_WIRED:
        return (
            f'{row.name} is a service command, served by the service at {row.route}. '
            f'This terminal is not wired to that route yet: {row.reason}'
        )
    if row.state is TransportState.BLOCKED:
        return f'{row.name} is a service command, and the service has no endpoint for it yet: {row.reason}'
    if row.state is TransportState.DELEGATED:
        # A delegated row has a sender: reaching this sentence would mean the
        # dispatcher refused a command it could have served.
        raise ValueError(f'{row.name} is delegated and has a sender, so it has nothing to refuse')
    raise ValueError(f'{row.name} runs here, so it has nothing to refuse')


__all__ = ['TRANSPORT', 'Transport', 'TransportState', 'transport_for', 'unavailable_notice']
