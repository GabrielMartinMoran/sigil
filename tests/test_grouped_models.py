"""Multi-account model selection and persisted favourites."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Input

from src.engine.preferences import PreferencesStore
from src.engine.providers.domain.model_capability import ModelCapability, ModelCatalog
from src.ui.widgets.grouped_model_screen import AccountCatalog, GroupedModelScreen


def test_should_persist_favourites_by_account_and_model(tmp_path: Path) -> None:
    store = PreferencesStore(tmp_path / 'ui.json')
    prefs = store.load().with_favorite('account-1', 'shared/model')
    store.save(prefs)
    assert store.load().model_favorites == (('account-1', 'shared/model'),)
    assert store.load().with_favorite('account-1', 'shared/model').model_favorites == ()


def test_should_filter_models_by_provider_and_toggle_favourite(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = PreferencesStore(tmp_path / 'ui.json')
        groups = [
            AccountCatalog('one', 'work', ModelCatalog((ModelCapability('alpha'),))),
            AccountCatalog('two', 'personal', ModelCatalog((ModelCapability('beta'),))),
        ]
        screen = GroupedModelScreen(
            groups,
            store.load().model_favorites,
            on_favorites=lambda favorites: store.save(store.load().with_favorites(favorites)),
        )

        class Harness(App[None]):
            def compose(self) -> ComposeResult:
                yield Input()

            def on_mount(self) -> None:
                self.push_screen(screen)

        app = Harness()
        async with app.run_test() as pilot:
            await pilot.press('ctrl+f')
            assert store.load().model_favorites == (('two', 'beta'),)
            assert screen.visible_models[0].model.name == 'beta'
            screen.query_one(Input).value = 'work'
            await pilot.pause()
            assert [row.model.name for row in screen.visible_models] == ['alpha']
            await pilot.press('enter')
            await pilot.pause()
            assert not isinstance(app.screen, GroupedModelScreen)

    asyncio.run(scenario())


def test_should_allow_custom_model_for_a_provider_with_no_catalogue() -> None:
    async def scenario() -> None:
        groups = [AccountCatalog('one', 'offline', ModelCatalog())]
        screen = GroupedModelScreen(groups, (), on_favorites=lambda _: None)

        class Harness(App[None]):
            def compose(self) -> ComposeResult:
                yield Input()

            def on_mount(self) -> None:
                self.push_screen(screen)

        async with Harness().run_test() as pilot:
            screen.query_one(Input).value = 'vendor/new-model'
            await pilot.pause()
            assert any(
                row.model.name == 'vendor/new-model' and row.account_id == 'one' for row in screen.visible_models
            )

    asyncio.run(scenario())
