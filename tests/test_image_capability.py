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
import json
from pathlib import Path

from PIL import Image
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import src.ui.widgets.prompt_input as prompt_module
from src.engine.providers.domain.model_capability import ModelCapability
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.provider_variant import ProviderVariant
from src.engine.providers.domain.selection import Selection
from src.engine.providers.snapshot import ModelSnapshot
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from tests.ui_helpers import render_text

FIXTURES = Path(__file__).parent / 'fixtures'


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


def test_a_text_only_model_refuses_the_paste_and_names_the_way_out(tmp_path):
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


def test_a_multimodal_model_accepts_the_paste(tmp_path):
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


def test_with_no_model_configured_the_paste_is_allowed(tmp_path):
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


# --- the catalogues ---------------------------------------------------------


def test_the_openrouter_catalogue_reads_the_declared_modalities():
    from src.engine.providers.catalog.openrouter_source import parse_models

    payload = {
        'data': [
            {'id': 'sees', 'architecture': {'input_modalities': ['text', 'image']}},
            {'id': 'blind', 'architecture': {'input_modalities': ['text']}},
            {'id': 'silent'},
        ]
    }

    models = {model.name: model for model in parse_models(payload)}

    assert models['sees'].supports_images
    assert not models['blind'].supports_images
    # Silence is not a refusal; see the module docstring.
    assert models['silent'].supports_images


def test_the_opencode_catalogue_reads_the_same_declaration_in_its_own_shape():
    from src.engine.providers.catalog.opencode_source import parse_models

    payload = {
        'data': [
            {'id': 'sees', 'modalities': {'input': ['text', 'image']}},
            {'id': 'blind', 'modalities': {'input': ['text']}},
            {'id': 'silent'},
        ]
    }

    models = {model.name: model for model in parse_models(payload)}

    assert models['sees'].supports_images
    assert not models['blind'].supports_images
    assert models['silent'].supports_images


def test_the_opencode_catalogue_reads_the_mime_qualified_modalities_it_really_publishes():
    """The live payload says ``image/png``, not ``image``, and that broke every model.

    Only the two alpha models that happen to list the bare word were considered
    multimodal; Claude, GPT-5 and DeepSeek were all reported as text-only, so the
    composer refused pastes the provider would have accepted.
    """
    from src.engine.providers.catalog.opencode_source import parse_models

    payload = {
        'data': [
            {'id': 'sees', 'modalities': {'input': ['text', 'image/jpeg', 'image/png', 'image/webp']}},
            {'id': 'blind', 'modalities': {'input': ['text']}},
            {'id': 'bare', 'modalities': {'input': ['text', 'image']}},
            {'id': 'silent'},
        ]
    }

    models = {model.name: model for model in parse_models(payload)}

    assert models['sees'].supports_images
    assert models['bare'].supports_images
    assert not models['blind'].supports_images
    assert models['silent'].supports_images


def test_the_recorded_opencode_catalogue_keeps_its_vision_models_multimodal():
    """The recorded payload spells every image as a MIME type, and all of them were read as blind."""
    from src.engine.providers.catalog.opencode_source import parse_models

    payload = json.loads((FIXTURES / 'opencode_models.json').read_text(encoding='utf-8'))
    models = {model.name: model for model in parse_models(payload)}

    assert models['claude-sonnet-4-6'].supports_images
    assert models['gpt-5'].supports_images
    assert models['gemini-3.8-flash'].supports_images
    assert not models['big-pickle'].supports_images


def test_the_openrouter_catalogue_also_accepts_a_mime_qualified_declaration():
    """OpenRouter publishes the bare word today; the MIME form must not flip it to blind."""
    from src.engine.providers.catalog.openrouter_source import parse_models

    payload = {'data': [{'id': 'sees', 'architecture': {'input_modalities': ['text', 'image/png']}}]}

    models = {model.name: model for model in parse_models(payload)}

    assert models['sees'].supports_images


def test_the_openai_catalogue_uses_the_bundled_profile():
    from src.engine.providers.catalog.openai_source import parse_models

    profiles = {'blind': {'supports_image_input': False}, 'sees': {}}
    payload = {'data': [{'id': 'blind'}, {'id': 'sees'}]}

    models = {model.name: model for model in parse_models(payload, profiles.get)}

    assert not models['blind'].supports_images
    assert models['sees'].supports_images


def test_the_flag_survives_a_saved_selection():
    """A cold start has to refuse a paste without reaching the network."""
    selection = Selection(account_id='a', model='vision', images=True)

    restored = Selection.from_dict(selection.to_dict())

    assert restored is not None and restored.images


def test_an_old_selection_without_the_flag_is_treated_as_capable():
    """Selections written before images existed must not start refusing pastes."""
    restored = Selection.from_dict({'account_id': 'a', 'model': 'm'})

    assert restored is not None and restored.images
