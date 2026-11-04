"""Picks the builder for an account and keeps the model-swapping wrapper.

Two responsibilities that belong together because both answer "which model is the
agent using right now":

- ``BuilderRegistry``: an account plus its credential → a ``ModelBuilder``.
- ``ModelRouter``: the ``WrapperModel`` the agent holds, whose wrapped model the
  service swaps between turns. That keeps ``agent.model.context_window`` — which
  the panel, the meter and the debug view read — correct after a switch,
  without rebuilding the agent and losing its tools and capabilities.
"""

from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.wrapper import WrapperModel

from src.engine.providers.domain.auth_method import AuthMethod
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_error import ProviderError
from src.engine.providers.domain.secret import OAuthSecret
from src.engine.providers.factory.builder import ModelBuilder


class BuilderRegistry:
    """Resolves the builder that can serve an account."""

    def __init__(self, builders: tuple[ModelBuilder, ...]) -> None:
        self._builders = builders

    def for_account(self, account: ProviderAccount, secret: object) -> ModelBuilder:
        """The builder able to serve ``account`` with ``secret``.

        The credential decides between two builders of the same family, so an
        account whose stored secret was replaced stops mismatching its configured
        auth method silently.

        Raises:
            ProviderError: When no builder handles the pair.
        """
        wanted = AuthMethod.OAUTH if isinstance(secret, OAuthSecret) else AuthMethod.API_KEY
        for builder in self._builders:
            if builder.supports(account) and builder.auth is wanted:
                return builder
        raise ProviderError(
            f"There is no builder for an '{account.provider_label}' account using {wanted.label.lower()}."
        )

    @property
    def builders(self) -> tuple[ModelBuilder, ...]:
        return self._builders


class ModelRouter(WrapperModel):
    """The agent's model, whose target the service replaces between turns.

    ``WrapperModel.__init__`` runs ``infer_model`` on its argument, which rejects
    an already-built ``Model``; seeding ``Model.__init__`` directly and assigning
    ``wrapped`` avoids that and keeps every delegated member working.
    """

    def __init__(self, initial: Model) -> None:
        Model.__init__(self)
        self.wrapped: Model = initial

    def use(self, model: Model) -> None:
        """Replaces the model the next request will use."""
        self.wrapped = model

    @property
    def current(self) -> Model:
        return self.wrapped
