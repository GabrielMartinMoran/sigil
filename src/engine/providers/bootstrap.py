"""Wiring of the provider layer.

One function so the composition root (``src/sigil.py``) and the tests build the
same object graph with different inputs. It reads no globals: every path and
every collaborator arrives as an argument.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic_ai.models import Model

from src.engine.providers.catalog.catalog_service import ModelCatalogService
from src.engine.providers.catalog.probe import probe_credential
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.factory.builder import ModelBuilder
from src.engine.providers.factory.openai_builder import OpenAIKeyBuilder, OpenAIOAuthBuilder
from src.engine.providers.factory.opencode_builder import OpenCodeBuilder
from src.engine.providers.factory.openrouter_builder import OpenRouterBuilder
from src.engine.providers.factory.registry import BuilderRegistry, ModelRouter
from src.engine.providers.service import CredentialProbe, IsBusy, ModelService
from src.engine.providers.store.account_store import FileAccountStore
from src.engine.providers.store.secret_store import FileSecretStore
from src.engine.providers.store.state_store import FileStateStore

ACCOUNTS_FILE = 'accounts.json'
CREDENTIALS_FILE = 'credentials.json'
STATE_FILE = 'state.json'


@dataclass(frozen=True, slots=True)
class ProviderPaths:
    """Where the provider layer keeps its three files."""

    directory: Path

    @property
    def accounts(self) -> Path:
        return self.directory / ACCOUNTS_FILE

    @property
    def credentials(self) -> Path:
        return self.directory / CREDENTIALS_FILE

    @property
    def state(self) -> Path:
        return self.directory / STATE_FILE


def build_registry(session_id: str, oauth_provider_factory=None) -> BuilderRegistry:
    """Every builder, in resolution order.

    Args:
        session_id: Stable conversation id used by the OpenCode headers.
        oauth_provider_factory: Builds the ChatGPT-plan provider. Passing it keeps
            this function free of the credential store.
    """
    builders: list[ModelBuilder] = [
        OpenRouterBuilder(),
        OpenCodeBuilder(session_id),
        OpenAIKeyBuilder(),
    ]
    if oauth_provider_factory is not None:
        builders.append(OpenAIOAuthBuilder(oauth_provider_factory))
    return BuilderRegistry(tuple(builders))


def build_service(
    paths: ProviderPaths,
    *,
    session_id: str,
    initial_model: Model,
    oauth_provider_factory=None,
    probe: CredentialProbe | None = None,
    is_busy: IsBusy | None = None,
    catalog: ModelCatalogService | None = None,
) -> ModelService:
    """Assembles the service over the given paths.

    Args:
        paths: Where the three files live. Their parent must be writable.
        session_id: Stable id of the conversation, for the OpenCode session header.
        initial_model: Model the router holds before any selection is applied.
            The composition root builds a placeholder so the agent is always
            runnable, even with zero configured accounts.
        oauth_provider_factory: Enables the browser sign-in route.
        probe: Credential check. Defaults to the real one; tests pass a fake.
        is_busy: Whether a turn is running. Defaults to "never busy".
        catalog: Catalogue service. Defaults to the real one.
    """
    return ModelService(
        accounts=FileAccountStore(paths.accounts),
        secrets=FileSecretStore(paths.credentials),
        state=FileStateStore(paths.state),
        catalog=catalog or ModelCatalogService(),
        registry=build_registry(session_id, oauth_provider_factory),
        router=ModelRouter(initial_model),
        probe=probe if probe is not None else _default_probe,
        is_busy=is_busy if is_busy is not None else (lambda: False),
    )


async def _default_probe(account: ProviderAccount, api_key: str):
    return await probe_credential(account, api_key)
