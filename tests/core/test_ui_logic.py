"""Tests for the pure UI logic (command registry and effort wording)."""

from src.engine.commands import match_commands


def test_match_commands_requires_slash():
    assert match_commands('hello') == []


def test_match_commands_filters_by_prefix():
    """Everything matches `/`; the order groups session commands before client ones.

    The palette is one list, but it is two kinds of thing: what the domain answers and
    what only this window can. Listing them together in catalogue order keeps the
    session commands where a user looks for them.
    """
    names = [command.name for command in match_commands('/')]
    assert names == [
        # the domain answers these
        '/new',
        '/load',
        '/agent',
        '/provider',
        '/model',
        '/default-models',
        '/effort',
        '/external-files',
        '/mcp',
        '/context',
        '/rollback',
        # only this client can
        '/reasoning-visibility',
        '/tools',
        '/theme',
        '/sidebar',
        '/alerts',
        '/debug',
        '/help',
        '/quit',
    ]

    assert [c.name for c in match_commands('/n')] == ['/new']  # '/new' only: '/model' does not start with '/n'
    assert [c.name for c in match_commands('/l')] == ['/load']
    assert match_commands('/zzz') == []


def test_match_commands_ignores_argument():
    assert [c.name for c in match_commands('/load something')] == ['/load']


def test_match_commands_covers_the_provider_commands():
    assert [c.name for c in match_commands('/prov')] == ['/provider']
    assert [c.name for c in match_commands('/mod')] == ['/model']
    assert [c.name for c in match_commands('/eff')] == ['/effort']
