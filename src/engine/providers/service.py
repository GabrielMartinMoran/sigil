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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic_ai.models import Model

from src.engine.providers.catalog.catalog_service import ModelCatalogService
from src.engine.providers.catalog.probe import ProbeResult
from src.engine.providers.catalog.static_source import fallback_capability
from src.engine.providers.domain.auth_method import AuthMethod, auth_methods_for
from src.engine.providers.domain.model_capability import ModelCapability, ModelCatalog
from src.engine.providers.domain.provider_account import ProviderAccount, normalize_alias
from src.engine.providers.domain.provider_error import (
    AccountNotFound,
    BusyError,
    CredentialError,
    LastAccountError,
    NoAccountError,
    ProviderError,
    SecretRejectedError,
)
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.provider_variant import ProviderVariant, variants_for
from src.engine.providers.domain.secret import ApiKeySecret, OAuthSecret, SecretMaterial
from src.engine.providers.domain.selection import Selection
from src.engine.providers.domain.thinking import EFFORT_LEVELS, ThinkingChoice, parse_choice
from src.engine.providers.factory.registry import BuilderRegistry, ModelRouter
from src.engine.providers.snapshot import EMPTY_SNAPSHOT, AccountView, ModelSnapshot
from src.engine.providers.store.account_store import FileAccountStore
from src.engine.providers.store.secret_store import SecretStore
from src.engine.providers.store.state_store import FileStateStore

CredentialProbe = Callable[[ProviderAccount, str], Awaitable[ProbeResult]]
"""Checks a key. Injected so tests never touch the network."""

IsBusy = Callable[[], bool]
"""Whether a turn is running. Swapping models mid-turn is refused."""


class LoginFlow(Protocol):
    """A browser sign-in, as the service needs to see it."""

    async def sign_in(self, account: ProviderAccount) -> OAuthSecret:
        """Blocks until the browser hands back credentials.

        Raises:
            ProviderError: When the sign-in fails or is abandoned.
        """
        ...


@dataclass
class ModelService:
    """Configured accounts and the model the agent uses."""

    accounts: FileAccountStore
    secrets: SecretStore
    state: FileStateStore
    catalog: ModelCatalogService
    registry: BuilderRegistry
    router: ModelRouter
    probe: CredentialProbe | None = None
    is_busy: IsBusy = field(default=lambda: False)

    _probe_results: dict[str, ProbeResult] = field(default_factory=dict, init=False, repr=False)

    # --- accounts ----------------------------------------------------------

    def list_accounts(self) -> tuple[ProviderAccount, ...]:
        """Every configured account, active one first."""
        accounts = list(self.accounts.load())
        active_id = self._active_account_id()
        accounts.sort(key=lambda account: (account.id != active_id, account.kind.value, account.alias.lower()))
        return tuple(accounts)

    def account_views(self) -> tuple[AccountView, ...]:
        """Rows for the account list screen."""
        active_id = self._active_account_id()
        return tuple(AccountView(account=account, active=account.id == active_id) for account in self.list_accounts())

    def find(self, alias: str) -> ProviderAccount:
        """Look up by alias.

        Raises:
            AccountNotFound: When no account has that alias.
        """
        account = self.accounts.find_alias(alias)
        if account is None:
            raise AccountNotFound(alias)
        return account

    def last_probe(self, account_id: str) -> ProbeResult | None:
        """What the last credential check said, for the UI to report a warning."""
        return self._probe_results.get(account_id)

    async def add_api_key(
        self,
        kind: ProviderKind,
        alias: str,
        api_key: str,
        *,
        variant: ProviderVariant = ProviderVariant.DEFAULT,
    ) -> ModelSnapshot:
        """Validates and stores a new API-key account, then makes it active.

        Raises:
            AliasError: For an empty, malformed or duplicate alias.
            CredentialError: When the key is empty or malformed.
            SecretRejectedError: When the provider rejects the key.
        """
        if AuthMethod.API_KEY not in auth_methods_for(kind):
            raise ProviderError(f'{kind.label} does not take an API key.')
        cleaned = normalize_alias(alias)
        self.accounts.ensure_free_alias(cleaned)
        key = _clean_key(api_key)

        account = ProviderAccount.create(cleaned, kind, variant, AuthMethod.API_KEY)
        await self._check_credential(account, key)

        self.secrets.put(account.id, ApiKeySecret(key=key, alias=account.alias))
        self._save([*self.accounts.load(), account])
        return self.select(account.alias, await self._default_model(account, key), 'default')

    async def add_oauth(self, kind: ProviderKind, alias: str, flow: LoginFlow) -> ModelSnapshot:
        """Runs a browser sign-in and stores the resulting account.

        Raises:
            AliasError: For an empty, malformed or duplicate alias.
            ProviderError: When the sign-in fails or grants no plan usage.
        """
        if AuthMethod.OAUTH not in auth_methods_for(kind):
            raise ProviderError(f'{kind.label} does not support a browser sign-in.')
        cleaned = normalize_alias(alias)
        self.accounts.ensure_free_alias(cleaned)

        account = ProviderAccount.create(cleaned, kind, ProviderVariant.DEFAULT, AuthMethod.OAUTH)
        signed_in = await flow.sign_in(account)
        if not signed_in.plan_usage_granted:
            raise ProviderError(
                'That account signed in but did not grant plan usage, so requests would be refused. '
                'Run the sign-in again and approve access to your plan.'
            )

        self.secrets.put(account.id, signed_in)
        if signed_in.client_id:
            self.state.remember_client_id(account.alias, signed_in.client_id)
        self._save([*self.accounts.load(), account])
        return self.select(
            account.alias,
            await self._default_model(account, signed_in.access_token),
            'default',
        )

    def remove(self, alias: str) -> None:
        """Removes an account and its credential.

        When it was the active one, the first remaining account takes over.

        Raises:
            AccountNotFound: When no account has that alias.
            LastAccountError: When it is the only one.
        """
        account = self.find(alias)
        remaining = [item for item in self.accounts.load() if item.id != account.id]
        if not remaining:
            raise LastAccountError()

        was_active = account.id == self._active_account_id()
        self.secrets.delete(account.id)
        self.catalog.invalidate(account.id)
        self._probe_results.pop(account.id, None)
        self._save(remaining)

        if not was_active:
            return
        successor = remaining[0]
        model = successor.last_model or next(iter(self._cached_model_names(successor)), '')
        if model:
            self.select(successor.alias, model, 'default')
        else:
            self.state.set_last_selection(None)

    def rename(self, alias: str, new_alias: str) -> ProviderAccount:
        """Changes the label. The id, and therefore saved sessions, stay valid.

        Raises:
            AccountNotFound: When no account has ``alias``.
            AliasError: For an empty, malformed or duplicate ``new_alias``.
        """
        account = self.find(alias)
        cleaned = normalize_alias(new_alias)
        self.accounts.ensure_free_alias(cleaned, ignore_id=account.id)

        renamed = account.renamed(cleaned)
        self._save([renamed if item.id == account.id else item for item in self.accounts.load()])
        return renamed

    # --- models ------------------------------------------------------------

    async def catalog_for(self, alias: str | None = None) -> ModelCatalog:
        """The catalogue of ``alias``, or of the active account."""
        account = self.find(alias) if alias else self._require_active_account()
        return await self.catalog.models(account, secret=self._api_key_for(account))

    def select(self, alias: str, model: str, thinking: ThinkingChoice = 'default') -> ModelSnapshot:
        """Makes ``model`` of ``alias`` the active model.

        Raises:
            AccountNotFound: When no account has that alias.
            BusyError: While a turn is running.
            CredentialError: When the account has no usable credential.
            ProviderError: When the reasoning level does not parse.
        """
        account = self.find(alias)
        if self.is_busy():
            raise BusyError()

        choice = _parse_thinking(thinking)
        capability = self._capability_for(account, model)
        selection = Selection(
            account_id=account.id,
            model=model,
            thinking=_fit_thinking(choice, capability),
            window=capability.context_window,
            efforts=capability.efforts,
            images=capability.supports_images,
        )

        built = self._build(account, model, capability, self._secret_for(account))
        self.router.use(built)

        self._save([account.with_model(model) if item.id == account.id else item for item in self.accounts.load()])
        self.state.set_last_selection(selection)
        return self.snapshot()

    def set_thinking(self, thinking: ThinkingChoice) -> ModelSnapshot:
        """Changes the reasoning level of the active model.

        Raises:
            NoAccountError: When nothing is configured.
            BusyError: While a turn is running.
            ProviderError: When the model does not accept that level.
        """
        current = self.state.last_selection()
        if current is None:
            raise NoAccountError()
        if self.is_busy():
            raise BusyError()

        choice = _parse_thinking(thinking)
        account = self.accounts.find_id(current.account_id)
        if account is None:
            raise NoAccountError()

        capability = self._capability_for(account, current.model)
        if choice not in {'default', 'off'} and not capability.allows(choice):
            raise ProviderError(
                f'{current.model} does not accept the {choice} level; it accepts {capability.efforts_label}.'
            )

        self.state.set_last_selection(current.with_thinking(choice))
        return self.snapshot()

    # --- state -------------------------------------------------------------

    def snapshot(self) -> ModelSnapshot:
        """What the UI paints right now. Never raises.

        The catalogue is a network resource, so the stored selection carries the
        last known window and effort levels. Falling back to them is what makes a
        cold start show the real context size instead of ``?``.
        """
        selection = self.state.last_selection()
        if selection is None:
            return EMPTY_SNAPSHOT
        account = self.accounts.find_id(selection.account_id)
        if account is None:
            return EMPTY_SNAPSHOT

        fetched = self.catalog.cached(account)
        capability = fetched.find(selection.model) if fetched is not None else None
        if capability is None:
            capability = ModelCapability(
                name=selection.model,
                context_window=selection.window,
                efforts=selection.efforts,
                supports_images=selection.images,
            )
        notice = None
        if fetched is not None and fetched.error:
            notice = f'The model list could not be refreshed ({fetched.error}); the menu is shortened.'

        return ModelSnapshot(
            account=account,
            selection=selection,
            capability=capability,
            window=capability.context_window,
            efforts=capability.efforts,
            images=capability.supports_images,
            notice=notice,
        )

    def is_configured(self) -> bool:
        return self.state.last_selection() is not None

    async def refresh_capability(self) -> ModelSnapshot:
        """Fetches the catalogue once so the snapshot knows the window and efforts.

        A cold start has the selection but not the capabilities: they live in the
        catalogue, which is a network resource. Without this, the side panel shows
        ``?`` and the effort picker offers nothing until a turn or `/model` happens
        to fetch the list.

        Silent on failure: an offline start must keep working with the stored
        values, not block on a notice the user cannot act on.
        """
        selection = self.state.last_selection()
        if selection is None:
            return EMPTY_SNAPSHOT
        account = self.accounts.find_id(selection.account_id)
        if account is None or self.catalog.cached(account) is not None:
            return self.snapshot()

        catalog = await self.catalog.models(account, secret=self._api_key_for(account))
        found = catalog.find(selection.model)
        if found is not None:
            self.state.set_last_selection(
                selection.with_capability(found.context_window, found.efforts, images=found.supports_images)
            )
        return self.snapshot()

    def resolve(self, session_selection: Selection | None = None) -> Selection | None:
        """Which selection a turn should use, or ``None`` when there is none.

        Order: the session's own, then the last one used, then the first account
        that remembers a model. A selection whose account was removed is skipped
        rather than trusted, because the credential is gone with it.
        """
        if session_selection is not None and self.accounts.find_id(session_selection.account_id) is not None:
            return session_selection

        stored = self.state.last_selection()
        if stored is not None and self.accounts.find_id(stored.account_id) is not None:
            return stored

        for account in self.accounts.load():
            if account.last_model:
                return Selection(account_id=account.id, model=account.last_model)
        return None

    def stored_window(self) -> int | None:
        """Last known context window, without touching the network.

        The composition root calls this before the UI exists, so the meter can
        budget the first turn of a cold start.
        """
        selection = self.state.last_selection()
        if selection is None or selection.window is None:
            return None
        return selection.window

    def apply(self, selection: Selection) -> ModelSnapshot:
        """Points the router at ``selection`` without touching the stored state.

        Used when loading a session: the conversation dictates the model, and that
        becomes the new last selection so the next session inherits it too.
        """
        account = self.accounts.find_id(selection.account_id)
        if account is None:
            raise NoAccountError()
        return self.select(account.alias, selection.model, selection.thinking)

    def current_model(self) -> Model:
        """The model the agent talks to.

        This is the **router**, never the concrete model. The agent holds it for
        the whole session, and swapping the router's target is what makes a switch
        take effect without rebuilding the agent. Returning ``router.current``
        here would hand the agent a fixed model, and every later ``select`` would
        silently do nothing until a restart.
        """
        return self.router

    # --- internals ---------------------------------------------------------

    def _active_account_id(self) -> str | None:
        selection = self.state.last_selection()
        return selection.account_id if selection is not None else None

    def _require_active_account(self) -> ProviderAccount:
        selection = self.state.last_selection()
        if selection is None:
            raise NoAccountError()
        account = self.accounts.find_id(selection.account_id)
        if account is None:
            raise NoAccountError()
        return account

    def _secret_for(self, account: ProviderAccount) -> SecretMaterial:
        secret = self.secrets.get(account.id)
        if secret is None:
            raise CredentialError(
                f"The credential for '{account.alias}' is missing from the store. Remove the account and add it again."
            )
        return secret

    def _api_key_for(self, account: ProviderAccount) -> str | None:
        """The key needed to read a catalogue, when the store has one."""
        secret = self.secrets.get(account.id)
        return secret.key if isinstance(secret, ApiKeySecret) else None

    def _build(
        self,
        account: ProviderAccount,
        model: str,
        capability: ModelCapability,
        secret: SecretMaterial,
    ) -> Model:
        """Builds the model, turning a rejected name into a domain error.

        The SDK raises its own ``UserError`` for a name the route cannot accept (an
        OpenRouter id without the ``vendor/`` prefix, for instance). That is a user
        mistake with a readable message, not a crash, so it is translated here
        instead of escaping to the UI as a traceback.
        """
        builder = self.registry.for_account(account, secret)
        try:
            return builder.build(account, model, capability, secret)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"'{model}' cannot be used with '{account.alias}': {exc}") from exc

    def _capability_for(self, account: ProviderAccount, model: str) -> ModelCapability:
        fetched = self.catalog.cached(account)
        found = fetched.find(model) if fetched is not None else None
        if found is not None:
            return found
        # Nothing fetched: the static table may still know this model, and its
        # efforts are about to be persisted, so a guess of "none" would stick.
        static = fallback_capability(model)
        if static is not None:
            return static
        # A model no catalogue has described (Go, free text, cold cache) is allowed
        # with unknown capabilities: refusing it would make a valid model unusable
        # just because a list could not be fetched.
        return ModelCapability(name=model)

    def _save(self, accounts: list[ProviderAccount]) -> None:
        try:
            self.accounts.save(accounts)
        except OSError as exc:
            raise ProviderError(f'Could not save the provider list: {exc}') from exc

    async def _check_credential(self, account: ProviderAccount, api_key: str) -> None:
        if self.probe is None:
            return
        result = await self.probe(account, api_key)
        self._probe_results[account.id] = result
        if result.is_rejected:
            raise SecretRejectedError(result.detail)

    async def _default_model(self, account: ProviderAccount, secret: str | None) -> str:
        catalog = await self.catalog.models(account, secret=secret)
        if catalog.models:
            return catalog.models[0].name
        raise ProviderError(f"No model could be determined for '{account.alias}'. Pick one with /model.")

    def _cached_model_names(self, account: ProviderAccount):
        fetched = self.catalog.cached(account)
        if fetched is None:
            return iter(())
        return (model.name for model in fetched.models)


def _clean_key(api_key: str) -> str:
    key = api_key.strip()
    if not key:
        raise CredentialError('The API key cannot be empty.')
    if len(key) > 4096 or any(character.isspace() for character in key):
        raise CredentialError('That does not look like an API key: it contains spaces or is far too long.')
    return key


def _parse_thinking(thinking: ThinkingChoice) -> ThinkingChoice:
    choice = parse_choice(str(thinking))
    if choice is None:
        levels = ', '.join(EFFORT_LEVELS)
        raise ProviderError(f"'{thinking}' is not a reasoning level. Use one of: {levels}, off, default.")
    return choice


def _fit_thinking(choice: ThinkingChoice, capability: ModelCapability) -> ThinkingChoice:
    """Keeps a level only when the model accepts it.

    Switching models should not fail because the previous one accepted a level the
    new one does not; it falls back to the model default instead.
    """
    if choice in {'default', 'off'}:
        return choice
    return choice if capability.allows(choice) else 'default'


def kinds_for_ui() -> tuple[ProviderKind, ...]:
    """Kinds the add form offers, in display order."""
    return (ProviderKind.OPENROUTER, ProviderKind.OPENCODE, ProviderKind.OPENAI)


def variants_of(kind: ProviderKind) -> tuple[ProviderVariant, ...]:
    return variants_for(kind)


def auth_methods_of(kind: ProviderKind) -> tuple[AuthMethod, ...]:
    return auth_methods_for(kind)
