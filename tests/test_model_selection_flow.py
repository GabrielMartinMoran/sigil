"""Model picker commits account and effort together only after confirmation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.providers.domain.model_capability import ModelCapability, ModelCatalog
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.effort_screen import EffortChoice, EffortScreen
from src.ui.widgets.grouped_model_screen import GroupedModelChoice, GroupedModelScreen


def _account(account_id: str, alias: str, provider: str = 'OpenRouter') -> SimpleNamespace:
    """The account the picker reads: id and alias to choose, provider to show."""
    return SimpleNamespace(id=account_id, alias=alias, provider_label=provider)


def test_should_choose_other_account_and_effort_atomically(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
        app = SigilApp(agent=Agent(TestModel(), instructions='x'), store=store, session=store.create_session())
        model = ModelCapability('m', efforts=('low', 'high'))
        service = Mock()
        service.is_configured.return_value = True
        service.list_accounts.return_value = [
            _account('a', 'first'),
            _account('b', 'second'),
        ]
        service.catalog_for = AsyncMock(return_value=ModelCatalog((model,)))
        service.select.return_value = SimpleNamespace(display_name='m · second · OpenRouter')
        app._model_service = service
        app._after_model_change = Mock()
        app.push_screen_wait = AsyncMock(side_effect=[GroupedModelChoice('b', 'second', model), EffortChoice('high')])
        chat = SimpleNamespace(add_notice=AsyncMock())
        await app.show_models(chat)
        assert isinstance(app.push_screen_wait.await_args_list[0].args[0], GroupedModelScreen)
        assert isinstance(app.push_screen_wait.await_args_list[1].args[0], EffortScreen)
        service.select.assert_called_once_with('second', 'm', 'high')

    asyncio.run(scenario())


def test_should_leave_current_selection_unchanged_when_effort_cancelled(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
        app = SigilApp(agent=Agent(TestModel(), instructions='x'), store=store, session=store.create_session())
        model = ModelCapability('m')
        service = Mock()
        service.is_configured.return_value = True
        service.list_accounts.return_value = [_account('a', 'first')]
        service.catalog_for = AsyncMock(return_value=ModelCatalog((model,)))
        app._model_service = service
        app.push_screen_wait = AsyncMock(side_effect=[GroupedModelChoice('a', 'first', model), None])
        await app.show_models(SimpleNamespace(add_notice=AsyncMock()))
        service.select.assert_not_called()

    asyncio.run(scenario())


def test_should_keep_failed_provider_available_for_manual_model_entry(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
        app = SigilApp(agent=Agent(TestModel(), instructions='x'), store=store, session=store.create_session())
        service = Mock()
        service.list_accounts.return_value = [_account('a', 'offline')]
        service.catalog_for = AsyncMock(side_effect=RuntimeError('network unavailable'))
        app._model_service = service
        app.push_screen_wait = AsyncMock(return_value=None)
        await app.show_models(SimpleNamespace(add_notice=AsyncMock()))
        screen = app.push_screen_wait.await_args.args[0]
        assert isinstance(screen, GroupedModelScreen)
        assert screen._catalogs[0].alias == 'offline'
        assert screen._catalogs[0].catalog.error

    asyncio.run(scenario())
