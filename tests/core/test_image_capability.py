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

import json
from pathlib import Path

from src.engine.providers.domain.selection import Selection

FIXTURES = Path(__file__).parents[1] / 'fixtures'


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
