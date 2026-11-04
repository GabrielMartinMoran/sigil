"""Model metadata queried dynamically from OpenRouter.

The maximum context (context window) does not come in pydantic-ai's static profile
for arbitrary OpenRouter models, so we get it from the public
``/api/v1/models`` endpoint and cache it per process.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

_MODELS_URL = 'https://openrouter.ai/api/v1/models'
_TIMEOUT = 6.0
_cache: dict[str, ModelInfo | None] = {}


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    canonical_slug: str
    context_length: int | None


def parse_model_info(data: list[dict], model_id: str) -> ModelInfo | None:
    """Looks for ``model_id`` in the OpenRouter model list."""
    candidate = model_id.removeprefix('~')
    for entry in data:
        ids = {entry.get('id'), entry.get('canonical_slug')}
        if candidate in ids or model_id in ids:
            context = entry.get('context_length')
            return ModelInfo(
                id=entry.get('id', ''),
                name=entry.get('name', ''),
                canonical_slug=entry.get('canonical_slug', ''),
                context_length=context if isinstance(context, int) and context > 0 else None,
            )
    return None


async def fetch_model_info(model_id: str) -> ModelInfo | None:
    """Returns the model info (cached) or ``None`` if it could not be fetched."""
    if model_id in _cache:
        return _cache[model_id]

    info: ModelInfo | None = None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(_MODELS_URL)
            response.raise_for_status()
            data = response.json().get('data', [])
        if isinstance(data, list):
            info = parse_model_info(data, model_id)
    except httpx.HTTPError, ValueError:
        info = None

    _cache[model_id] = info
    return info


def context_usage_percent(used_tokens: int | None, context_length: int | None) -> float | None:
    """Percentage of the context window used, or ``None`` if information is missing."""
    if not context_length or used_tokens is None:
        return None
    return max(0.0, used_tokens / context_length * 100.0)
