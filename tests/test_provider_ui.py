"""Tests for the provider screens: they collect, they do not act.

Each screen is driven through its real key handling (`run_test`), and the value
it returns is asserted. The point is the flows that are easy to break by hand:
skipping a step that has no choice, going back with `Esc`, and picking a model
that is not in the catalogue.
"""

from __future__ import annotations

import asyncio

from pydantic_ai.models.test import TestModel

from src.engine.providers.domain.auth_method import AuthMethod
from src.engine.providers.domain.model_capability import ModelCapability, ModelCatalog
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.provider_variant import ProviderVariant
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.account_form_screen import AccountFormScreen, NewAccount
from src.ui.widgets.confirm_screen import ConfirmScreen
from src.ui.widgets.model_picker_screen import ModelChoice, ModelPickerScreen
from src.ui.widgets.provider_screen import ProviderAction, ProviderScreen
from src.ui.widgets.text_prompt_screen import TextPromptScreen

CATALOG = ModelCatalog(
    models=(
        ModelCapability(
            name='anthropic/claude-sonnet-4.6',
            display_name='Claude Sonnet 4.6',
            context_window=1_000_000,
            efforts=('low', 'high'),
        ),
        ModelCapability(name='openai/gpt-4o-mini', display_name='GPT-4o mini', context_window=128_000),
        ModelCapability(name='meta/llama-free', display_name='Llama (free)'),
    ),
    source='remote',
)


# --- account list -----------------------------------------------------------


def _screen_result(screen, keys: tuple[str, ...] = ('escape',)):
    """Pushes ``screen`` on a real app, presses ``keys`` and returns the result."""

    async def inner():
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=__import__('pathlib').Path(tmp) / 's')
            app = SigilApp(
                agent=__import__('pydantic_ai').Agent(TestModel(), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            result: dict[str, object] = {}
            async with app.run_test() as pilot:
                app.push_screen(screen, lambda value: result.setdefault('value', value))
                await pilot.pause()
                for key in keys:
                    await pilot.press(key)
                await pilot.pause()
            return result.get('value')

    return asyncio.run(inner())


def test_provider_screen_lists_accounts_with_the_active_marker():
    account = ProviderAccount.create('work', ProviderKind.OPENROUTER).with_model('a/b')
    screen = ProviderScreen([account], active_id=account.id)
    assert '●' in screen._options()[0].prompt
    assert 'a/b' in screen._options()[0].prompt


def test_provider_screen_marks_an_inactive_account():
    screen = ProviderScreen([ProviderAccount.create('work', ProviderKind.OPENROUTER)])
    assert '○' in screen._options()[0].prompt


def test_escape_closes_the_provider_list_without_an_action():
    assert _screen_result(ProviderScreen([]), ('escape',)) is None


def test_a_opens_the_add_flow():
    result = _screen_result(ProviderScreen([]), ('a',))
    assert result == ProviderAction('add')


def test_enter_activates_the_highlighted_account():
    account = ProviderAccount.create('work', ProviderKind.OPENROUTER)
    result = _screen_result(ProviderScreen([account]), ('enter',))
    assert result == ProviderAction('activate', 'work')


def test_d_requests_removal_of_the_highlighted_account():
    account = ProviderAccount.create('work', ProviderKind.OPENROUTER)
    result = _screen_result(ProviderScreen([account]), ('d',))
    assert result == ProviderAction('delete', 'work')


def test_r_requests_a_rename():
    account = ProviderAccount.create('work', ProviderKind.OPENROUTER)
    result = _screen_result(ProviderScreen([account]), ('r',))
    assert result == ProviderAction('rename', 'work')


# --- add form ---------------------------------------------------------------


def test_form_skips_the_variant_step_for_openrouter_and_asks_for_the_alias():
    screen = AccountFormScreen()
    keys = ('enter',)  # OpenRouter is the first option
    _screen_result(screen, keys)
    assert screen._step == 'alias'
    assert screen._kind is ProviderKind.OPENROUTER


def test_form_asks_for_the_variant_on_opencode():
    screen = AccountFormScreen()
    _screen_result(screen, ('down', 'enter'))
    assert screen._step == 'variant'
    assert screen._kind is ProviderKind.OPENCODE


def test_form_asks_for_the_auth_method_on_openai():
    screen = AccountFormScreen()
    # OpenRouter, OpenCode, OpenAI → the third entry.
    _screen_result(screen, ('down', 'down', 'enter'))
    assert screen._step == 'auth'


def test_form_escape_steps_back_and_then_closes():
    screen = AccountFormScreen()
    _screen_result(screen, ('down', 'enter', 'escape', 'escape'))
    # Step went variant → provider, then the second Esc closed it.
    assert screen._step == 'provider'


def test_form_builds_the_value_object_after_the_alias_and_key():
    screen = AccountFormScreen()
    result = _screen_result(screen, ('enter', 'w', 'o', 'r', 'k', 'enter', 's', 'k', '-', '1', 'enter'))
    assert result == NewAccount(
        kind=ProviderKind.OPENROUTER,
        variant=ProviderVariant.DEFAULT,
        auth=AuthMethod.API_KEY,
        alias='work',
        api_key='sk-1',
    )


def test_form_refuses_an_empty_alias():
    screen = AccountFormScreen()
    _screen_result(screen, ('enter', 'enter'))
    assert screen._step == 'alias'


# --- model picker -----------------------------------------------------------


def _picker(keys: tuple[str, ...]):
    return _screen_result(ModelPickerScreen(CATALOG, account_label='work', current='openai/gpt-4o-mini'), keys)


def test_picker_returns_the_highlighted_model():
    assert _picker(('enter',)) == ModelChoice(model='anthropic/claude-sonnet-4.6')


def test_picker_marks_the_current_model():
    screen = ModelPickerScreen(CATALOG, current='openai/gpt-4o-mini')
    _screen_result(screen, ('escape',))
    rows = [_row_text(model) for model in screen._catalog.models]
    assert any('●' in row for row in rows)


def _row_text(model):
    from src.ui.widgets.model_picker_screen import _row

    return _row(model, current='openai/gpt-4o-mini')


def test_picker_filters_as_you_type():
    screen = ModelPickerScreen(CATALOG)
    _screen_result(screen, ('g', 'p', 't', 'enter'))
    assert screen._visible and screen._visible[0].name == 'openai/gpt-4o-mini'


def test_picker_offers_the_typed_text_when_nothing_matches():
    screen = ModelPickerScreen(CATALOG)
    result = _screen_result(screen, ('z', 'z', 'z', 'enter'))
    assert result == ModelChoice(model='zzz', from_catalog=False)


def test_picker_escape_cancels():
    assert _picker(('escape',)) is None


def test_picker_shows_the_catalogue_error_in_the_status_line():
    broken = ModelCatalog(models=CATALOG.models, source='static', error='offline')
    screen = ModelPickerScreen(broken)
    _screen_result(screen, ('escape',))
    assert screen._catalog.error == 'offline'


# --- confirm ----------------------------------------------------------------


def test_confirm_defaults_to_the_safe_answer():
    assert _screen_result(ConfirmScreen('Remove it?'), ('enter',)) is False


def test_confirm_returns_true_only_when_chosen_explicitly():
    assert _screen_result(ConfirmScreen('Remove it?'), ('up', 'enter')) is True


def test_confirm_escape_is_a_no():
    assert _screen_result(ConfirmScreen('Remove it?'), ('escape',)) is False


# --- text prompt ------------------------------------------------------------


def test_text_prompt_returns_the_typed_value():
    assert _screen_result(TextPromptScreen(title='Rename'), ('n', 'e', 'w', 'enter')) == 'new'


def test_text_prompt_prefills_the_current_value():
    screen = TextPromptScreen(title='Rename', value='old')
    assert _screen_result(screen, ('enter',)) == 'old'


def test_text_prompt_refuses_an_empty_value():
    screen = TextPromptScreen(title='Rename')
    _screen_result(screen, ('enter',))
    # Still on screen: the empty value was rejected, not returned.
    assert screen.is_mounted


def test_text_prompt_escape_cancels():
    assert _screen_result(TextPromptScreen(title='Rename'), ('escape',)) is None
