"""The parse_models helper of the providers layer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.core.providers.catalog.source import as_list
from src.core.providers.domain.accepts_image_input import accepts_image_input
from src.core.providers.domain.model_capability import ModelCapability
from src.core.providers.domain.normalize_efforts import normalize_efforts

DIALECT = 'chat-completions'


def parse_models(payload: Any) -> tuple[ModelCapability, ...]:
    """Turns the payload into capabilities. Duplicate routes collapse on ``id``."""
    seen: set[str] = set()
    models: list[ModelCapability] = []
    for entry in as_list(payload):
        model = _parse_entry(entry)
        if model is None or model.name in seen:
            continue
        seen.add(model.name)
        models.append(model)
    return tuple(models)


def _parse_entry(entry: dict[str, Any]) -> ModelCapability | None:
    name = entry.get('id')
    if not isinstance(name, str) or not name:
        return None
    context = entry.get('context_length')
    reasoning = entry.get('reasoning')
    efforts: Sequence[object] = ()
    if isinstance(reasoning, dict):
        raw = reasoning.get('supported_efforts')
        if isinstance(raw, list):
            efforts = raw
    return ModelCapability(
        name=name,
        display_name=_clean_name(entry.get('name')) or name,
        context_window=context if isinstance(context, int) and context > 0 else None,
        efforts=normalize_efforts(efforts),
        dialect=DIALECT,
        supports_tools=_supports_tools(entry),
        supports_images=_supports_images(entry),
    )


def _clean_name(raw: object) -> str:
    """``"Anthropic: Claude Sonnet 4.6"`` → ``"Claude Sonnet 4.6"``.

    The provider prefix is redundant in a list that is already scoped to one
    account, and it is what makes the rows too long to read.
    """
    if not isinstance(raw, str):
        return ''
    _, separator, rest = raw.partition(': ')
    return rest.strip() if separator else raw.strip()


def _supports_tools(entry: dict[str, Any]) -> bool:
    params = entry.get('supported_parameters')
    if not isinstance(params, list):
        # Unknown is treated as capable: hiding a model is worse than a 400 the
        # user can read.
        return True
    return 'tools' in params


def _supports_images(entry: dict[str, Any]) -> bool:
    """Whether the model takes image input, from its declared modalities.

    OpenRouter publishes the bare ``image`` family today, but the decision is
    delegated so a MIME-qualified list from the same endpoint reads the same.
    """
    architecture = entry.get('architecture')
    if not isinstance(architecture, dict):
        return True
    return accepts_image_input(architecture.get('input_modalities'))
