"""Tests for the pure UI logic (command registry and effort wording)."""

from src.engine.commands import match_commands
from src.engine.providers.domain.model_capability import ModelCapability
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.selection import Selection
from src.engine.providers.snapshot import ModelSnapshot
from src.ui.model_view_mixin import effort_label
from tests.ui_helpers import render_text


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


def test_assistant_block_renders_markdown_and_collapses_reasoning():
    from src.ui.widgets.chat_log import AssistantBlock

    collapsed = AssistantBlock(reasoning_visible=False)
    collapsed.add_thinking('thinking hard')
    collapsed.add_text('# Hello')
    collapsed.finalize()
    text = render_text(collapsed._build())
    # Collapsed reasoning keeps its marker without exposing the hidden content.
    assert 'Reasoning' in text
    assert 'thinking hard' not in text
    assert 'Hello' in text

    expanded = AssistantBlock(reasoning_visible=True)
    expanded.add_thinking('thinking hard')
    expanded.add_text('# Hello')
    expanded.finalize()
    assert 'thinking hard' in render_text(expanded._build())


def test_assistant_block_without_reasoning_is_just_markdown():
    from src.ui.widgets.chat_log import AssistantBlock, LinkedMarkdown

    block = AssistantBlock(reasoning_visible=False)
    block.add_text('# Hello')
    block.finalize()
    assert isinstance(block._build(), LinkedMarkdown)


def test_assistant_block_without_content_is_empty_text():
    from rich.text import Text

    from src.ui.widgets.chat_log import AssistantBlock

    block = AssistantBlock()
    block.finalize()
    assert isinstance(block._build(), Text)
    assert block._build().plain == ''


def test_collapsed_reasoning_reports_how_much_it_hides():
    from src.ui.widgets.chat_log import AssistantBlock

    block = AssistantBlock(reasoning_visible=False)
    block.add_thinking('one\ntwo\nthree')
    block.finalize()
    text = render_text(block._build())
    assert '3 lines' in text
    assert '/reasoning-visibility' not in text


def test_unavailable_reasoning_is_only_shown_when_expanded():
    from src.ui.widgets.chat_log import AssistantBlock

    block = AssistantBlock()
    block.add_thinking('')
    assert 'Reasoning' in render_text(block._build())
    assert 'unavailable' not in render_text(block._build())
    block.set_reasoning_visible(True)
    assert 'unavailable' in render_text(block._build())
    block.set_reasoning_visible(False)
    assert 'unavailable' not in render_text(block._build())
