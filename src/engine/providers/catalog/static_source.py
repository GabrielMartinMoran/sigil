"""A last-resort list so a first run without network is not an empty menu.

These are hand-kept and deliberately short: their only job is to let someone pick a
reasonable model when the catalogue cannot be reached.

They carry the efforts each model accepts, because that value is **persisted with
the selection**: a fallback that claimed "no effort control" would write that lie
into the state file and the effort picker would stay empty even after the
catalogue came back. They also carry the context window, for the same reason.

Matching the real catalogues is not the goal here (the remote list wins as soon as
it is reachable); not lying to the UI about a model we do know is.
"""

from __future__ import annotations

from src.engine.providers.domain.model_capability import ModelCapability
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.thinking import EFFORT_LEVELS

_ALL = EFFORT_LEVELS
"""Every level, for the models that accept the full range."""

_WIDE = ('low', 'medium', 'high', 'max')
"""The common Claude/GPT range: no `minimal`, no `xhigh`."""

OPENROUTER_FALLBACK: tuple[ModelCapability, ...] = (
    ModelCapability(
        name='anthropic/claude-sonnet-4.6',
        display_name='Claude Sonnet 4.6',
        context_window=1_000_000,
        efforts=_WIDE,
    ),
    ModelCapability(
        name='anthropic/claude-haiku-4.5',
        display_name='Claude Haiku 4.5',
        context_window=200_000,
        efforts=_WIDE,
    ),
    ModelCapability(
        name='openai/gpt-5.1',
        display_name='GPT-5.1',
        context_window=400_000,
        efforts=('low', 'medium', 'high'),
    ),
    ModelCapability(
        name='google/gemini-3.8-flash',
        display_name='Gemini 3.8 Flash',
        context_window=1_000_000,
        efforts=('low', 'medium', 'high'),
    ),
)

OPENCODE_FALLBACK: tuple[ModelCapability, ...] = (
    ModelCapability(
        name='claude-sonnet-4-6',
        display_name='Claude Sonnet 4.6',
        context_window=1_000_000,
        efforts=_WIDE,
    ),
    ModelCapability(
        name='claude-haiku-4-5',
        display_name='Claude Haiku 4.5',
        context_window=200_000,
        efforts=_WIDE,
    ),
    ModelCapability(name='gpt-5', display_name='GPT-5', context_window=400_000, efforts=_ALL),
    ModelCapability(name='glm-5.3', display_name='GLM-5.3', context_window=200_000, efforts=('low', 'high', 'max')),
)

OPENAI_FALLBACK: tuple[ModelCapability, ...] = (
    ModelCapability(name='gpt-5.1', display_name='gpt-5.1', context_window=400_000, efforts=_ALL),
    ModelCapability(name='gpt-5', display_name='gpt-5', context_window=400_000, efforts=_ALL),
    ModelCapability(name='gpt-4.1', display_name='gpt-4.1', context_window=1_000_000),
    ModelCapability(name='o3', display_name='o3', context_window=200_000, efforts=('low', 'medium', 'high')),
)


def fallback_models(account: ProviderAccount) -> tuple[ModelCapability, ...]:
    """The static list for ``account``."""
    if account.kind is ProviderKind.OPENROUTER:
        return OPENROUTER_FALLBACK
    if account.kind is ProviderKind.OPENCODE:
        # Both variants share the ids; Go just has no endpoint to list them.
        return OPENCODE_FALLBACK
    return OPENAI_FALLBACK


def fallback_capability(name: str) -> ModelCapability | None:
    """The static entry for ``name``, searched across every family.

    Used when the selection names a model the remote catalogue could not describe:
    a stale or offline start still knows the efforts of the models we ship, instead
    of persisting "no effort control".
    """
    for table in (OPENROUTER_FALLBACK, OPENCODE_FALLBACK, OPENAI_FALLBACK):
        for model in table:
            if model.name == name:
                return model
    return None
