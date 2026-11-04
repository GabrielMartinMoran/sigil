"""Metadatos del modelo consultados dinámicamente a OpenRouter.

El contexto máximo (ventana de contexto) no viene en el perfil estático de
pydantic-ai para modelos arbitrarios de OpenRouter, así que lo obtenemos del
endpoint público ``/api/v1/models`` y lo cacheamos por proceso.
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
    """Busca ``model_id`` dentro de la lista de modelos de OpenRouter."""
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
    """Devuelve la info del modelo (con cache) o ``None`` si no se pudo obtener."""
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
    """Porcentaje de ventana de contexto usada, o ``None`` si falta información."""
    if not context_length or used_tokens is None:
        return None
    return max(0.0, used_tokens / context_length * 100.0)
