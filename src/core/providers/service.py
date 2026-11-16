"""The use cases the UI drives: adding, listing, choosing and inspecting models.

This is the only entry point for the rest of the application. It owns three
things no adapter may own:

- **Validation order.** An alias is checked before a network probe, and a probe
  rejection stops the account from being written, so the list never holds a
  credential the provider already refused.
- **Selection resolution.** Which account and model a turn should use, in the
  documented order (session → last used → first account that remembers a model).
- **Composition.** It builds the router's models, so nothing else needs to know
  which builder applies to which provider.

Nothing here touches the UI, and nothing here reads the environment.
"""

from __future__ import annotations

from src.core.providers.domain.auth_method import AuthMethod
from src.core.providers.domain.auth_methods_for import auth_methods_for
from src.core.providers.domain.provider_kind import ProviderKind
from src.core.providers.domain.provider_variant import ProviderVariant
from src.core.providers.domain.variants_for import variants_for

"""Checks a key. Injected so tests never touch the network."""

"""Whether a turn is running. Swapping models mid-turn is refused."""


def kinds_for_ui() -> tuple[ProviderKind, ...]:
    """Kinds the add form offers, in display order."""
    return (ProviderKind.OPENROUTER, ProviderKind.OPENCODE, ProviderKind.OPENAI)


def variants_of(kind: ProviderKind) -> tuple[ProviderVariant, ...]:
    return variants_for(kind)


def auth_methods_of(kind: ProviderKind) -> tuple[AuthMethod, ...]:
    return auth_methods_for(kind)
