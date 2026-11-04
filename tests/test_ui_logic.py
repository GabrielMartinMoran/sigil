"""Tests for the pure UI logic (command registry and effort wording)."""

from src.engine.commands import match_commands
from src.engine.providers.domain.model_capability import ModelCapability
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.selection import Selection
from src.engine.providers.snapshot import ModelSnapshot
from src.ui.model_view_mixin import effort_label


def test_match_commands_requires_slash():
    assert match_commands('hello') == []


def test_match_commands_filters_by_prefix():
    names = [command.name for command in match_commands('/')]
    assert names == [
        '/new',
        '/load',
        '/provider',
        '/model',
        '/effort',
        '/sidebar',
        '/mcp',
        '/context',
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


def test_effort_label_falls_back_to_the_selection():
    account = ProviderAccount.create('work', ProviderKind.OPENROUTER)
    snapshot = ModelSnapshot(
        account=account,
        selection=Selection(account_id=account.id, model='m', thinking='high'),
        capability=ModelCapability(name='m'),
    )
    assert effort_label(snapshot, 'high') == 'high'
    assert effort_label(snapshot, 'bogus') == 'high'
    assert effort_label(ModelSnapshot(), 'bogus') == 'default'


def test_assistant_renderable_uses_markdown_for_text():
    from rich.console import Group
    from rich.markdown import Markdown
    from rich.text import Text

    from src.ui.widgets.chat_log import build_assistant_renderable

    assert isinstance(build_assistant_renderable('thinking', ''), Text)
    assert isinstance(build_assistant_renderable('', '# Hello'), Markdown)
    assert isinstance(build_assistant_renderable('thinking', 'text'), Group)
    assert isinstance(build_assistant_renderable('', ''), Text)
