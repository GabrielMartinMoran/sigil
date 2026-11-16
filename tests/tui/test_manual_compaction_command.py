"""The terminal sends explicit compaction modes to the session owner."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.ui.mixins.commands_mixin import CommandsMixin


def test_compact_llm_dispatches_to_service() -> None:
    client = SimpleNamespace(compact=AsyncMock(return_value={'status': 'complete', 'before': 100, 'after': 50}))
    app = SimpleNamespace(_client=client, refresh_service_state=AsyncMock())
    chat = SimpleNamespace(add_notice=AsyncMock())
    asyncio.run(CommandsMixin.handle_compact(app, '--llm', chat))
    client.compact.assert_awaited_once_with('llm')
    assert any('100' in str(call) and '50' in str(call) for call in chat.add_notice.await_args_list)


def test_compact_rejects_invalid_flags_without_contacting_service() -> None:
    client = SimpleNamespace(compact=AsyncMock())
    app = SimpleNamespace(_client=client)
    chat = SimpleNamespace(add_notice=AsyncMock())
    asyncio.run(CommandsMixin.handle_compact(app, '--llm --deterministic', chat))
    client.compact.assert_not_awaited()
    assert 'Usage:' in str(chat.add_notice.await_args)
