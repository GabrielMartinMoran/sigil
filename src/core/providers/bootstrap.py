"""Wiring of the provider layer.

One function so the composition root (``src/sigil.py``) and the tests build the
same object graph with different inputs. It reads no globals: every path and
every collaborator arrives as an argument.
"""

from __future__ import annotations

from pydantic_ai.models import Model

from src.core.providers.catalog.model_catalog_service import ModelCatalogService
from src.core.providers.catalog.plan_transport import resolver_for
from src.core.providers.catalog.probe import probe_credential
from src.core.providers.catalog.probe_result import ProbeResult
from src.core.providers.domain.provider_account import ProviderAccount
from src.core.providers.factory.builder_registry import BuilderRegistry
from src.core.providers.factory.model_builder import ModelBuilder
from src.core.providers.factory.model_router import ModelRouter
from src.core.providers.factory.oauth_provider_factory import OAuthProviderFactory
from src.core.providers.factory.open_a_i_key_builder import OpenAIKeyBuilder
from src.core.providers.factory.open_a_i_o_auth_builder import OpenAIOAuthBuilder
from src.core.providers.factory.opencode_builder import OpenCodeBuilder
from src.core.providers.factory.openrouter_builder import OpenRouterBuilder
from src.core.providers.model_service import CredentialProbe, IsBusy, ModelService
from src.core.providers.provider_paths import ProviderPaths
from src.core.providers.store.file_secret_store import FileSecretStore
from src.core.providers.store.sqlite_account_store import SqliteAccountStore
from src.core.providers.store.sqlite_state_store import SqliteStateStore


def build_registry(session_id: str, oauth_provider_factory: OAuthProviderFactory | None = None) -> BuilderRegistry:
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
    oauth_provider_factory: OAuthProviderFactory | None = None,
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
        oauth_provider_factory: Enables the browser sign-in route, and lets the
            catalogue renew a plan account's token while listing its models.
        probe: Credential check. Defaults to the real one; tests pass a fake.
        is_busy: Whether a turn is running. Defaults to "never busy".
        catalog: Catalogue service. Defaults to one that reads a plan account
            through its provider, so loading models refreshes an expired token.
    """
    secrets = FileSecretStore(paths.credentials)
    return ModelService(
        accounts=SqliteAccountStore(paths.home),
        secrets=secrets,
        state=SqliteStateStore(paths.home),
        catalog=catalog or ModelCatalogService(transport_for=resolver_for(secrets, oauth_provider_factory)),
        registry=build_registry(session_id, oauth_provider_factory),
        router=ModelRouter(initial_model),
        probe=probe if probe is not None else _default_probe,
        is_busy=is_busy if is_busy is not None else (lambda: False),
    )


async def _default_probe(account: ProviderAccount, api_key: str) -> ProbeResult:
    return await probe_credential(account, api_key)
