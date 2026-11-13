"""The command catalogue declares who can serve each command, and it stays honest.

The registry lives in the engine and carries no widget, so it is easy for a UI-only
command to end up there without anyone noticing — which is exactly what happened: one
flat list mixed "create a session" with "repaint the theme". These tests lock the split
so adding a command forces a decision instead of a default.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from src.engine.commands import COMMANDS, CommandOwner, match_commands, owned_by
from src.ui.command_transport import (
    TRANSPORT,
    Transport,
    TransportState,
    transport_for,
    unavailable_notice,
)

HANDLERS = Path(__file__).resolve().parent.parent / 'src' / 'ui' / 'commands_mixin.py'

CLIENT_COMMANDS = frozenset(
    {
        '/reasoning-visibility',
        '/tools',
        '/alerts',
        '/theme',
        '/sidebar',
        '/debug',
        '/help',
        '/quit',
    }
)
"""The commands only this client can serve.

The lock is deliberate. A new entry here means the command configures the terminal and
not the domain; a new entry *anywhere else* means the core has to answer it, and the
core's API has to grow. Either way it is a decision, and this test is what makes it one.
"""


def test_every_command_declares_an_owner():
    assert all(command.owner in tuple(CommandOwner) for command in COMMANDS)


def test_the_two_owners_partition_the_catalogue():
    core = set(owned_by(CommandOwner.CORE))
    client = set(owned_by(CommandOwner.CLIENT))

    assert not core & client
    assert core | client == set(COMMANDS)


def test_the_client_commands_are_the_known_ones():
    assert {command.name for command in owned_by(CommandOwner.CLIENT)} == CLIENT_COMMANDS


def test_every_command_has_a_handler_and_every_handler_a_command():
    """A name in the catalogue with no handler is a menu entry that does nothing.

    The handler map is the only place the dispatch exists, so it is read from the
    source: the test runs in a process where the app is not built.
    """
    source = HANDLERS.read_text(encoding='utf-8')
    start = source.index('handlers = {')
    end = source.index('}', start)
    block = source[start:end]
    implemented = set(re.findall(r"'(/[a-z-]+)':", block))

    assert implemented == {command.name for command in COMMANDS}


def test_the_duplicate_name_check():
    names = [command.name for command in COMMANDS]
    assert len(names) == len(set(names))


@pytest.mark.parametrize('name', ['/new', '/load', '/rollback', '/context'])
def test_a_core_command_is_one_a_second_client_would_keep(name: str):
    command = next(item for item in COMMANDS if item.name == name)
    assert command.owner is CommandOwner.CORE


@pytest.mark.parametrize('name', ['/theme', '/sidebar', '/quit'])
def test_a_client_command_is_one_a_second_client_would_bring(name: str):
    command = next(item for item in COMMANDS if item.name == name)
    assert command.owner is CommandOwner.CLIENT


def test_matching_still_works_across_both_owners():
    """The palette shows everything: the client filters what it delegates."""
    assert [command.name for command in match_commands('/s')] == ['/sidebar']
    assert [command.name for command in match_commands('/l')] == ['/load']


def test_the_help_lists_both_groups_and_loses_nothing():
    from src.ui.help_text import HELP_TEXT

    assert 'Session' in HELP_TEXT
    assert 'This client' in HELP_TEXT
    for command in COMMANDS:
        assert command.name in HELP_TEXT


def test_the_help_never_promises_an_edit_the_service_can_refuse():
    """The one sentence `/help` prints is true in both modes, so it cannot lie in either.

    The old row said "Set the model each agent starts on, in the project or the user
    config", which is an edit an attached terminal cannot perform: the dispatcher
    refuses the command before any handler runs. What a default *is* and where it lives
    is true whether or not this window can write it, so the row names that instead.
    """

    from src.ui.help_text import HELP_TEXT

    command = next(command for command in COMMANDS if command.name == '/default-models')
    assert 'Set the model each agent starts on' not in command.description
    assert 'config' in command.description, 'the row still says where the defaults live'
    assert command.description in HELP_TEXT, 'the help prints the catalogue, not a second copy'


def test_a_listing_mark_comes_from_the_transport_row():
    """A listing marks what the table refuses, and invents no sentence of its own.

    One derivation, next to the refusal it shortens: the `/` menu and any future
    listing read it, so a row cannot be marked here and dispatched there.
    """

    from src.ui.command_transport import listing_mark

    row = transport_for('/default-models')
    assert row is not None
    assert row.state is TransportState.BLOCKED
    assert listing_mark('/default-models', attached=True), 'a blocked row says so where it is listed'
    assert listing_mark('/default-models', attached=False) == '', 'in process the command runs here'

    for name in ('/theme', '/model', '/context'):
        assert listing_mark(name, attached=True) == '', f'{name} runs here or travels from here'
    assert listing_mark('/nope', attached=True) == '', 'a name the catalogue never declared has no row'


# --- the transport table -----------------------------------------------------
#
# The catalogue says who *may* serve a command. Whether this terminal has a way to
# reach that server is a separate, client-side fact, and it lives in one table so
# the dispatcher never grows a second list of names. These tests are the seam
# between the two: a command added to the catalogue without a transport row, or a
# row without a command, fails here instead of silently doing nothing.


def test_every_command_has_a_transport_row_and_every_row_a_command():
    assert {row.name for row in TRANSPORT} == {command.name for command in COMMANDS}


def test_the_transport_table_names_each_command_once():
    names = [row.name for row in TRANSPORT]
    assert len(names) == len(set(names))


def test_a_transport_row_keeps_the_owner_the_catalogue_declared():
    owners = {command.name: command.owner for command in COMMANDS}
    assert all(row.owner is owners[row.name] for row in TRANSPORT)


def test_the_commands_that_run_here_are_exactly_the_client_ones():
    local = {row.name for row in TRANSPORT if row.state is TransportState.CLIENT}
    assert local == CLIENT_COMMANDS


def test_only_the_documented_gap_is_blocked():
    """Every command the service serves has a sender here, except one.

    The `blocked` state stays in the table because the decision has to be total: a
    command the service has no endpoint for must say that, not borrow the answer meant
    for a command that is merely not wired yet.

    `/default-models` is that command, and the exception is the point. It edits files
    the service owns, so a terminal that wrote them would be a second writer on one
    configuration — the failure the attached terminal exists to remove. There is no
    route to point a sender at, so the row is `blocked` and the command says so. The
    alternative, hiding it behind `not_wired`, would promise a sender that is not
    coming in this cut. When an endpoint lands, this list empties again.
    """
    blocked = {row.name for row in TRANSPORT if row.state is TransportState.BLOCKED}
    assert blocked == {'/default-models'}


def test_a_row_that_does_not_run_here_says_why_and_where():
    for row in TRANSPORT:
        if row.state is TransportState.CLIENT:
            assert row.reason == '', f'{row.name} runs here, so it has nothing to explain'
        else:
            assert row.reason, f'{row.name} must say why it is {row.state}'
    for row in TRANSPORT:
        if row.state in {TransportState.NOT_WIRED, TransportState.DELEGATED}:
            assert row.route is not None, f'{row.name} names the route it would use'
        if row.state is TransportState.BLOCKED:
            assert row.route is None, 'a blocked command has no route to name'


def test_an_unknown_command_has_no_row_at_all():
    """The dispatcher still owns "Unknown command": a name nobody planned is not a row."""

    assert transport_for('/nope') is None


def test_the_not_wired_notice_still_names_the_command_the_route_and_the_gap():
    """No row is `not_wired` now, so the sentence is proven on a row built for it.

    The state stays in the table because the decision has to be total: a command the
    service serves and this terminal has not wired yet must still say so precisely,
    instead of borrowing the answer meant for a command with no endpoint at all.
    """
    row = Transport(
        name='/model',
        owner=CommandOwner.CORE,
        state=TransportState.NOT_WIRED,
        route='/api/model',
        reason='the picker reads a catalogue this terminal cannot fetch.',
    )
    notice = unavailable_notice(row)

    assert '/model' in notice
    assert '/api/model' in notice
    assert 'not wired' in notice
    assert row.reason in notice


def test_the_blocked_notice_says_no_endpoint_rather_than_a_missing_setting():
    """The blocked sentence must not send the user to configure what they have."""

    row = Transport(
        name='/provider',
        owner=CommandOwner.CORE,
        state=TransportState.BLOCKED,
        reason='the service has no accounts endpoint.',
    )
    notice = unavailable_notice(row)

    assert '/provider' in notice
    assert 'no endpoint' in notice
    assert 'Configure a provider' not in notice


def test_a_delegated_row_has_a_route_and_is_dispatched_to_its_handler():
    """Delegated means wired: a route to send to, and a handler that runs.

    The refusal sentence is not for these rows any more. Asking one for it is a bug
    in the dispatcher, and the table says so instead of answering with a sentence
    about a sender that exists.
    """

    delegated = {row.name for row in TRANSPORT if row.state is TransportState.DELEGATED}
    assert delegated == {
        '/agent',
        '/context',
        '/effort',
        '/external-files',
        '/load',
        '/mcp',
        '/model',
        '/new',
        '/provider',
        '/rollback',
    }
    for row in TRANSPORT:
        if row.state is TransportState.DELEGATED:
            assert row.route is not None, f'{row.name} travels on a route'
            assert row.reason
            with pytest.raises(ValueError, match='has a sender'):
                unavailable_notice(row)

    # The choke point refuses only the two states with no way out, so a delegated
    # row falls through to the handler map below it.
    source = HANDLERS.read_text(encoding='utf-8')
    assert '{TransportState.NOT_WIRED, TransportState.BLOCKED}' in source


def test_the_transport_table_refuses_two_rows_for_one_command():
    """The import-time guard, proven: two rows for one command would be a silent winner.

    It fails where the table is built, not at the moment a user types the command.
    """
    from src.ui.command_transport import _covering

    duplicate = Transport('/theme', CommandOwner.CLIENT, TransportState.CLIENT)
    with pytest.raises(ValueError, match='more than once'):
        _covering((*TRANSPORT, duplicate))


def test_the_menu_marks_the_row_a_service_owns_and_only_then():
    """One listing, two windows: the mark is the table's, and it tracks the mode.

    Mounted on its own, so the assertion is about the menu's rule rather than about an
    app: the flag it is handed is the whole difference between a terminal that runs the
    command and one that can only say who does. `attached=False` is the in-process
    shape, where `/default-models` opens its screen, so a mark there would be a lie
    about a command that works.
    """
    from textual.app import App

    from src.ui.widgets.command_menu import CommandMenu
    from tests.ui_helpers import render_text

    class MenuHost(App[None]):
        def compose(self):
            yield CommandMenu()

    def rows(attached: bool) -> list[str]:
        async def inner() -> list[str]:
            app = MenuHost()
            async with app.run_test(size=(120, 40)):
                menu = app.query_one(CommandMenu)
                menu.update_matches(match_commands('/default'), attached=attached)
                return [render_text(option.prompt) for option in menu.options]

        return asyncio.run(inner())

    attached_rows = rows(True)
    assert len(attached_rows) == 1
    assert '/default-models' in attached_rows[0]
    assert 'the service owns it' in attached_rows[0]

    local_rows = rows(False)
    assert '/default-models' in local_rows[0]
    assert 'the service owns it' not in local_rows[0], 'in process the command runs here'
