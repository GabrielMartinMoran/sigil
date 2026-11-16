"""Whether the active model can read an image, and what happens when it cannot.

Two rules, and they pull in opposite directions on purpose:

- A catalogue that says `text` only is believed, and the paste is refused with a
  line that names the way out. Failing at the provider instead would spend a turn
  and a round trip to say the same thing.
- A catalogue that says nothing is assumed capable. Refusing a paste a model would
  have accepted is the worse mistake: the user cannot argue with a modal that
  claims the model is blind.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from PIL import Image
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import src.ui.widgets.panels.prompt_input as prompt_module
from src.core.providers.domain.model_capability import ModelCapability
from src.core.providers.domain.provider_account import ProviderAccount
from src.core.providers.domain.provider_kind import ProviderKind
from src.core.providers.domain.provider_variant import ProviderVariant
from src.core.providers.domain.selection import Selection
from src.core.providers.model_snapshot import ModelSnapshot
from src.core.sessions.session_store import SessionStore
from src.ui.sigil_app import SigilApp
from src.ui.widgets.chat.chat_log import ChatLog
from src.ui.widgets.panels.prompt_input import PromptInput
from tests.support.ui import render_text


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (20, 20), color='#3465a4').save(buffer, format='PNG')
    return buffer.getvalue()


class Service:
    """The smallest `ModelService` the app's snapshot path needs."""

    def __init__(self, *, images: bool, model: str = 'text-only') -> None:
        self._snapshot = ModelSnapshot(
            account=ProviderAccount(
                id='a', alias='acct', kind=ProviderKind.OPENROUTER, variant=ProviderVariant.DEFAULT
            ),
            selection=Selection(account_id='a', model=model, images=images),
            capability=ModelCapability(name=model, supports_images=images),
            images=images,
        )

    def snapshot(self) -> ModelSnapshot:
        return self._snapshot

    def is_configured(self) -> bool:
        return True

    def is_busy(self) -> bool:
        return False


def _app(tmp_path, *, service: Service | None = None) -> SigilApp:
    store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=store.create_session(),
        model_service=service,
    )


def _composer(app: SigilApp) -> PromptInput:
    return app.query_one(PromptInput)


def test_a_text_only_model_refuses_the_paste_and_names_the_way_out(tmp_path: Path) -> None:
    async def inner() -> None:
        app = _app(tmp_path, service=Service(images=False, model='text-small'))
        async with app.run_test() as pilot:
            composer = _composer(app)
            original = prompt_module.read_clipboard_image
            prompt_module.read_clipboard_image = lambda **_: _png()
            try:
                await pilot.press('ctrl+v')
            finally:
                prompt_module.read_clipboard_image = original

            assert composer.text == ''
            assert composer.attachments == ()
            notices = [render_text(child.render()) for child in app.query_one(ChatLog).children]
            assert any('text-small' in notice and '/model' in notice for notice in notices)

    asyncio.run(inner())


def test_a_multimodal_model_accepts_the_paste(tmp_path: Path) -> None:
    async def inner() -> None:
        app = _app(tmp_path, service=Service(images=True, model='vision'))
        async with app.run_test() as pilot:
            composer = _composer(app)
            original = prompt_module.read_clipboard_image
            prompt_module.read_clipboard_image = lambda **_: _png()
            try:
                await pilot.press('ctrl+v')
            finally:
                prompt_module.read_clipboard_image = original

            assert composer.text == '[Image #1]'

    asyncio.run(inner())


def test_with_no_model_configured_the_paste_is_allowed(tmp_path: Path) -> None:
    """The turn will fail on the missing credential; that is the message worth giving."""

    async def inner() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            assert app.accepts_images()
            composer = _composer(app)
            original = prompt_module.read_clipboard_image
            prompt_module.read_clipboard_image = lambda **_: _png()
            try:
                await pilot.press('ctrl+v')
            finally:
                prompt_module.read_clipboard_image = original

            assert composer.text == '[Image #1]'

    asyncio.run(inner())
