"""Tests for the pure UI logic (command registry and effort wording)."""

from src.core.providers.domain.model_capability import ModelCapability
from src.core.providers.domain.provider_account import ProviderAccount
from src.core.providers.domain.provider_kind import ProviderKind
from src.core.providers.domain.selection import Selection
from src.core.providers.snapshot import ModelSnapshot
from src.ui.effort_label import effort_label
from tests.support.ui import render_text


def test_effort_label_falls_back_to_the_selection() -> None:
    account = ProviderAccount.create('work', ProviderKind.OPENROUTER)
    snapshot = ModelSnapshot(
        account=account,
        selection=Selection(account_id=account.id, model='m', thinking='high'),
        capability=ModelCapability(name='m'),
    )
    assert effort_label(snapshot, 'high') == 'high'
    assert effort_label(snapshot, 'bogus') == 'high'
    assert effort_label(ModelSnapshot(), 'bogus') == 'default'


def test_assistant_block_renders_markdown_and_collapses_reasoning() -> None:
    from src.ui.widgets.assistant_block import AssistantBlock

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


def test_assistant_block_without_reasoning_is_just_markdown() -> None:
    from src.ui.widgets.assistant_block import AssistantBlock
    from src.ui.widgets.linked_markdown import LinkedMarkdown

    block = AssistantBlock(reasoning_visible=False)
    block.add_text('# Hello')
    block.finalize()
    assert isinstance(block._build(), LinkedMarkdown)


def test_assistant_block_without_content_is_empty_text() -> None:
    from rich.text import Text

    from src.ui.widgets.assistant_block import AssistantBlock

    block = AssistantBlock()
    block.finalize()
    assert isinstance(block._build(), Text)
    assert block._build().plain == ''


def test_collapsed_reasoning_reports_how_much_it_hides() -> None:
    from src.ui.widgets.assistant_block import AssistantBlock

    block = AssistantBlock(reasoning_visible=False)
    block.add_thinking('one\ntwo\nthree')
    block.finalize()
    text = render_text(block._build())
    assert '3 lines' in text
    assert '/reasoning-visibility' not in text


def test_unavailable_reasoning_is_only_shown_when_expanded() -> None:
    from src.ui.widgets.assistant_block import AssistantBlock

    block = AssistantBlock()
    block.add_thinking('')
    assert 'Reasoning' in render_text(block._build())
    assert 'unavailable' not in render_text(block._build())
    block.set_reasoning_visible(True)
    assert 'unavailable' in render_text(block._build())
    block.set_reasoning_visible(False)
    assert 'unavailable' not in render_text(block._build())
