"""Regression tests for the keyboard behaviour of the pickers and the menu.

Each test here exists because the interaction was broken in a way a screenshot
cannot catch:

- The arrows in the model picker did nothing, because the filter kept the focus
  and nothing forwarded them to the list.
- Tab in the command menu either did nothing or moved the focus away from the
  prompt instead of completing the command.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from textual.widgets import Input, OptionList

from src.engine.providers.domain.model_capability import ModelCapability, ModelCatalog
from src.engine.providers.domain.thinking import EFFORT_LEVELS
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.effort_screen import EffortScreen
from src.ui.widgets.model_picker_screen import ModelPickerScreen
from src.ui.widgets.prompt_input import PromptInput

CATALOG = ModelCatalog(
    models=(
        ModelCapability(name='a/one', display_name='One', context_window=100_000),
        ModelCapability(name='b/two', display_name='Two', context_window=200_000),
        ModelCapability(name='c/three', display_name='Three', context_window=300_000),
    ),
    source='remote',
)


def _app(tmp: str) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    return SigilApp(
        agent=Agent(TestModel(), instructions='x'),
        store=store,
        session=store.create_session(),
    )


def _drive(screen, keys: tuple[str, ...], inspect=None):
    """Pushes ``screen``, presses ``keys`` and returns what ``inspect`` says.

    ``inspect`` runs while the screen is still mounted, which is what the arrow and
    completion tests need: the result of dismissing would destroy the state.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            seen: dict[str, object] = {}
            async with app.run_test() as pilot:
                app.push_screen(screen)
                await pilot.pause()
                for key in keys:
                    await pilot.press(key)
                    await pilot.pause()
                if inspect is not None:
                    seen['value'] = inspect(screen)
            return seen.get('value')

    return asyncio.run(inner())


def _highlighted(screen) -> str | None:
    option = screen.query_one(OptionList).highlighted_option
    return None if option is None else str(option.id)


# --- model picker: arrows ---------------------------------------------------


def test_down_moves_the_highlight_in_the_model_picker():
    screen = ModelPickerScreen(CATALOG)
    assert _drive(screen, ('down',), _highlighted) == 'b/two'


def test_up_returns_to_the_previous_row():
    screen = ModelPickerScreen(CATALOG)
    assert _drive(screen, ('down', 'down', 'up'), _highlighted) == 'b/two'


def test_up_at_the_top_stays_at_the_top():
    screen = ModelPickerScreen(CATALOG)
    assert _drive(screen, ('up',), _highlighted) == 'a/one'


def test_down_at_the_bottom_stays_at_the_bottom():
    screen = ModelPickerScreen(CATALOG)
    assert _drive(screen, ('down', 'down', 'down', 'down'), _highlighted) == 'c/three'


def test_page_down_jumps_five_rows():
    long_catalog = ModelCatalog(
        models=tuple(ModelCapability(name=f'model-{index}') for index in range(12)),
        source='remote',
    )
    screen = ModelPickerScreen(long_catalog)
    assert _drive(screen, ('pagedown',), _highlighted) == 'model-5'


def test_arrows_still_move_while_a_filter_is_typed():
    """The bug: the arrows were dead as soon as the filter had focus."""
    screen = ModelPickerScreen(CATALOG)
    assert _drive(screen, ('t', 'down'), _highlighted) == 'c/three'


# --- model picker: Tab completes -------------------------------------------


def test_tab_completes_the_highlighted_model_without_choosing_it():
    screen = ModelPickerScreen(CATALOG)

    def field_value(_screen) -> str:
        return _screen.query_one('#picker-filter', Input).value

    assert _drive(screen, ('down', 'tab'), field_value) == 'b/two'


def test_tab_keeps_the_picker_open():
    screen = ModelPickerScreen(CATALOG)

    def still_open(_screen) -> bool:
        return _screen.is_mounted

    assert _drive(screen, ('tab',), still_open) is True


# --- model picker: Enter commits -------------------------------------------


def test_enter_after_arrows_picks_the_highlighted_model():
    screen = ModelPickerScreen(CATALOG)
    result = _screen_result(screen, ('down', 'enter'))
    assert result is not None
    assert result.model == 'b/two'


def _screen_result(screen, keys: tuple[str, ...]):
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            captured: dict[str, object] = {}
            async with app.run_test() as pilot:
                app.push_screen(screen, lambda value: captured.setdefault('v', value))
                await pilot.pause()
                for key in keys:
                    await pilot.press(key)
                    await pilot.pause()
            return captured.get('v')

    return asyncio.run(inner())


# --- effort screen ----------------------------------------------------------


def test_effort_screen_offers_only_the_accepted_levels():
    screen = EffortScreen(current='low', accepted=('low', 'high'), model='m')
    values = _drive(screen, (), lambda s: [str(o.id) for o in s.query_one(OptionList).options])
    assert values == ['default', 'low', 'high', 'off']


def test_effort_screen_offers_every_level_when_the_model_reports_all():
    screen = EffortScreen(current='default', accepted=EFFORT_LEVELS, model='m')
    values = _drive(screen, (), lambda s: [str(o.id) for o in s.query_one(OptionList).options])
    assert values == ['default', *EFFORT_LEVELS, 'off']


def test_effort_screen_highlights_the_current_level():
    screen = EffortScreen(current='high', accepted=('low', 'high'), model='m')
    assert _drive(screen, (), _highlighted) == 'high'


def test_effort_screen_moves_and_returns_the_level():
    screen = EffortScreen(current='default', accepted=('low', 'high'), model='m')
    result = _screen_result(screen, ('down', 'down', 'enter'))
    assert result is not None
    assert result.effort == 'high'


def test_effort_screen_cancels_with_escape():
    screen = EffortScreen(current='default', accepted=('low', 'high'), model='m')
    assert _screen_result(screen, ('escape',)) is None


# --- command menu: Tab completes -------------------------------------------


def test_tab_completes_the_highlighted_command():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                field = app.query_one(PromptInput)
                field.value = '/'
                await pilot.pause()
                await pilot.press('tab')
                await pilot.pause()
                assert field.value == '/new'

    asyncio.run(inner())


def test_tab_adds_a_trailing_space_for_commands_that_take_an_argument():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                field = app.query_one(PromptInput)
                field.value = '/mod'
                await pilot.pause()
                await pilot.press('tab')
                await pilot.pause()
                assert field.value == '/model '

    asyncio.run(inner())


def test_tab_does_not_run_the_command():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                field = app.query_one(PromptInput)
                field.value = '/'
                await pilot.pause()
                await pilot.press('tab')
                await pilot.pause()
                # Still on the main screen: completing is not executing.
                assert app.screen is app.screen_stack[0]
                assert field.value.startswith('/')

    asyncio.run(inner())


def test_tab_without_the_menu_does_not_steal_the_focus():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                field = app.query_one(PromptInput)
                field.value = ''
                await pilot.pause()
                await pilot.press('tab')
                await pilot.pause()
                assert app.focused is field

    asyncio.run(inner())


# --- shortcuts --------------------------------------------------------------


def test_the_app_binds_what_can_actually_reach_it():
    """Only the two view toggles are app bindings, and only because they have to be.

    The pickers read `ctrl+m` / `ctrl+t` in `PromptInput.on_key`: a global binding
    for `ctrl+m` would claim Enter on a terminal without the kitty protocol.
    `alt+t` joined `alt+d` when the tree of agents arrived: it is the same kind of
    key — a view toggle, read before the focused widget — and the user asked for it.
    """
    bindings = {binding.key for binding in SigilApp.BINDINGS}
    assert bindings == {'alt+d', 'alt+t'}


def test_the_pickers_are_reached_through_the_prompt_input():
    from src.ui.widgets.prompt_input import CTRL_EFFORT, CTRL_MODELS

    assert CTRL_MODELS == 'ctrl+m'
    assert CTRL_EFFORT == 'ctrl+t'


def test_ctrl_m_is_not_bound_because_it_is_enter():
    """Ctrl+M and Enter are the same byte in a terminal (CR, 0x0D).

    Binding it would swallow Enter, which is how a prompt is sent. The test
    documents the constraint so nobody "fixes" the shortcut back.
    """
    bindings = {binding.key for binding in SigilApp.BINDINGS}
    assert 'ctrl+m' not in bindings
    assert 'ctrl+t' not in bindings


# --- command menu: an argument closes it ------------------------------------


def test_typing_an_argument_closes_the_menu():
    """`Enter` on `/agent reviewer` runs the command, not the highlighted row.

    The menu completes the *name*. Left open while the argument is typed, it
    holds one row, `Enter` picks it, and the argument is silently dropped: with
    `TextArea` the text is edited in place instead of being submitted, so the
    submit path never gets the whole line.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                field = app.query_one(PromptInput)
                field.value = '/agent reviewer'
                await pilot.pause()
                assert app.menu_visible is False

    asyncio.run(inner())


def test_the_menu_reopens_when_the_argument_is_erased():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                field = app.query_one(PromptInput)
                field.value = '/agent reviewer'
                await pilot.pause()
                assert app.menu_visible is False

                field.value = '/agen'
                await pilot.pause()
                assert app.menu_visible is True

    asyncio.run(inner())
