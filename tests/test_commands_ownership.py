"""The command catalogue declares who can serve each command, and it stays honest.

The registry lives in the engine and carries no widget, so it is easy for a UI-only
command to end up there without anyone noticing — which is exactly what happened: one
flat list mixed "create a session" with "repaint the theme". These tests lock the split
so adding a command forces a decision instead of a default.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.engine.commands import COMMANDS, CommandOwner, match_commands, owned_by

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
