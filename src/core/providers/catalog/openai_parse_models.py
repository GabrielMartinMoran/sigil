"""The parse_models helper of the providers layer."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from src.core.providers.catalog.source import as_list
from src.core.providers.domain.accepts_image_input import accepts_image_input
from src.core.providers.domain.model_capability import ModelCapability
from src.core.providers.domain.normalize_efforts import normalize_efforts
from src.core.providers.domain.thinking import ThinkingEffort

DIALECT = 'responses'


PLAN_KEY = 'models'


IDENTIFIER_KEYS = ('slug', 'id')


Profile = Mapping[str, Any]


ProfileLookup = Callable[[str], Profile | None]


_FAMILIES_TO_HIDE = ('audio', 'realtime', 'transcribe', 'tts', 'image', 'embedding', 'moderation', 'dall-e', 'whisper')


def parse_models(payload: Any, profile_lookup: ProfileLookup) -> tuple[ModelCapability, ...]:
    """The catalogue, from whichever shape this endpoint answered.

    The rich shape wins on every field it publishes; the bundled profile fills the
    rest. A model the profile knows and the payload does not — an old `data` entry —
    keeps working exactly as before.
    """
    models: list[ModelCapability] = []
    seen: set[str] = set()
    for entry in _entries(payload):
        name = _name(entry)
        if name is None or name in seen or not _is_chat_model(name):
            continue
        seen.add(name)
        models.append(_capability(name, entry, profile_lookup(name)))
    models.sort(key=lambda model: model.name)
    return tuple(models)


def _entries(payload: Any) -> list[dict[str, Any]]:
    """The array of models, in either shape.

    ``models`` (plan) is checked first because it is the more specific key: a payload
    carrying it is answering the richer endpoint, whatever else it happens to include.
    """
    if isinstance(payload, dict) and isinstance(payload.get(PLAN_KEY), list):
        return [entry for entry in payload[PLAN_KEY] if isinstance(entry, dict)]
    return as_list(payload)


def _name(entry: dict[str, Any]) -> str | None:
    """The id to send to the API: ``slug`` (plan) or ``id`` (public API)."""
    for key in IDENTIFIER_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _capability(name: str, entry: dict[str, Any], profile: Profile | None) -> ModelCapability:
    return ModelCapability(
        name=name,
        display_name=_display_name(entry, name),
        context_window=_window(entry, profile),
        efforts=_efforts(entry, profile),
        dialect=DIALECT,
        supports_images=_supports_images(entry, profile),
    )


def _display_name(entry: dict[str, Any], name: str) -> str:
    """``GPT-6.1-Sol`` when the plan publishes one, otherwise the id.

    The id stays in `name`: that is what the API takes, and the picker shows the
    readable one next to it.
    """
    value = entry.get('display_name')
    return value.strip() if isinstance(value, str) and value.strip() else name


def _window(entry: dict[str, Any], profile: Profile | None) -> int | None:
    """The usable window: ``context_window`` first, the profile when absent.

    ``max_context_window`` is deliberately not the first choice: on the plan it is the
    ceiling reachable with experimental context, several times the default, and using
    it would make the meter budget a turn that the provider is going to truncate.
    """
    declared = entry.get('context_window')
    if isinstance(declared, int) and declared > 0:
        return declared
    window = profile.get('context_window') if profile else None
    return window if isinstance(window, int) and window > 0 else None


def _efforts(entry: dict[str, Any], profile: Profile | None) -> tuple[ThinkingEffort, ...]:
    """The reasoning levels, from the payload when it lists them.

    The plan publishes ``supported_reasoning_levels`` as objects —
    ``[{"effort": "low", "description": ...}, ...]`` — and reading them as strings
    silently produced an empty list, which is a model that looks like it cannot
    reason. The bundled profile is the fallback for the id-only shape.
    """
    levels = entry.get('supported_reasoning_levels')
    if isinstance(levels, list) and levels:
        return normalize_efforts(_level_names(levels))
    if profile and profile.get('supports_thinking'):
        return normalize_efforts(profile.get('openai_reasoning_efforts') or ())
    return ()


def _level_names(levels: list[Any]) -> tuple[str, ...]:
    """The effort of each level entry, in whatever shape it came."""
    names: list[str] = []
    for level in levels:
        if isinstance(level, str):
            names.append(level)
            continue
        if isinstance(level, dict):
            effort = level.get('effort') or level.get('level')
            if isinstance(effort, str):
                names.append(effort)
    return tuple(names)


def _supports_images(entry: dict[str, Any], profile: Profile | None) -> bool:
    """Whether the model takes image input.

    Three sources, most specific first: the modalities the endpoint declares, then
    the bundled profile, and finally ``True`` — leaving the default is what lets the
    paste through for a model nobody has described yet.
    """
    modalities = entry.get('input_modalities')
    if isinstance(modalities, list):
        return accepts_image_input(modalities)
    if profile is not None and 'supports_image_input' in profile:
        return bool(profile['supports_image_input'])
    return True


def _is_chat_model(name: str) -> bool:
    lowered = name.lower()
    return not any(fragment in lowered for fragment in _FAMILIES_TO_HIDE)
