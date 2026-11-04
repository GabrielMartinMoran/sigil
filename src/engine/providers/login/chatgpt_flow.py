"""The ChatGPT-plan sign-in, adapted to the service's ``LoginFlow`` port.

The heavy lifting is pydantic-ai's: ``OpenAIChatGPTOAuthFlow`` builds the PKCE
authorization URL, serves the one-shot loopback callback and exchanges the code.
What belongs here is everything the library deliberately leaves to the caller:

- **Persist the installation id.** OpenAI issues a ``client_id`` on the first
  sign-in of an account and expects it back afterwards; the host id identifies
  this installation across sign-ins. Both live in the state store.
- **Open the browser**, and degrade gracefully when there is none (headless
  session): the flow returns the URL so the UI can show it.

The loopback callback is pinned by OpenAI to ``127.0.0.1:1455``, so only one
sign-in can be waiting at a time. That is acceptable: a second concurrent sign-in
in one terminal is not a use case, and the error says so.
"""

from __future__ import annotations

import webbrowser
from collections.abc import Callable
from dataclasses import dataclass

from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTOAuthFlow

from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_error import LoginError
from src.engine.providers.domain.secret import OAuthSecret
from src.engine.providers.store.state_store import FileStateStore

APP_NAME = 'Sigil'

OpenBrowser = Callable[[str], bool]
"""Opens a URL. Returns whether a browser was launched."""


@dataclass(slots=True)
class ChatGPTLoginFlow:
    """``LoginFlow`` over ``OpenAIChatGPTOAuthFlow``."""

    state: FileStateStore
    on_url: Callable[[str], None] = lambda _url: None
    """Called with the authorization URL as soon as it exists, so the UI can show
    it even when the browser did not open."""

    open_browser: OpenBrowser | None = None
    reuse_registration: bool = True
    """Reuse the ``client_id`` from a previous sign-in of the same alias.

    A fresh registration for every sign-in leaves stale clients behind in the
    ChatGPT account, so the stored one wins when it exists.
    """

    async def sign_in(self, account: ProviderAccount) -> OAuthSecret:
        """Runs the browser sign-in and returns the credentials.

        Raises:
            LoginError: When the sign-in fails, is denied, or grants no plan usage.
        """
        flow = OpenAIChatGPTOAuthFlow(
            app_name=APP_NAME,
            host_id=self.state.installation_id(),
            client_id=self._reusable_client_id(account),
        )
        url = flow.authorization_url()
        self.on_url(url)
        self._launch(url)

        try:
            credentials = await flow.exchange_code_from_callback()
        except UserError as exc:
            raise LoginError(_explain(exc)) from exc
        except OSError as exc:
            raise LoginError(
                f'The sign-in could not listen on 127.0.0.1:1455 ({exc}). '
                'Close whatever is using that port and try again.'
            ) from exc

        return OAuthSecret(
            access_token=credentials.access_token,
            refresh_token=credentials.refresh_token,
            client_id=credentials.client_id,
            id_token=credentials.id_token,
            scope=credentials.scope,
            alias=account.alias,
        )

    def _reusable_client_id(self, account: ProviderAccount) -> str | None:
        if not self.reuse_registration:
            return None
        return self.state.stored_client_id(account.alias)

    def _launch(self, url: str) -> None:
        """Tries to open the browser. Failure is not an error: the URL is already shown."""
        opener = self.open_browser or _system_browser
        try:
            opener(url)
        except OSError:
            return


def _system_browser(url: str) -> bool:
    return webbrowser.open(url, new=2)


def _explain(exc: UserError) -> str:
    message = str(exc)
    if 'denied' in message.lower() or 'access_denied' in message.lower():
        return 'The sign-in was cancelled in the browser.'
    return f'The sign-in did not complete: {message}'
