"""Form to add a provider account.

A three-step wizard in one screen, because the choices are few and dependent:

1. **Provider** — OpenRouter, OpenCode or OpenAI.
2. **Product** — only OpenCode has two (Zen, Go); the step is skipped otherwise.
3. **Sign-in** — only OpenAI has two (API key, browser); skipped otherwise.

The screen returns a value object and never performs the add: the mixin owns the
service call, the probe and the error reporting, so `Esc` can always get out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from src.engine.providers.domain.auth_method import AuthMethod, auth_methods_for
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.provider_variant import ProviderVariant, variants_for
from src.ui.widgets.provider_modal import ProviderModal

Step = Literal['provider', 'variant', 'auth', 'alias', 'secret']

KEY_ENTRY = 'key'
BROWSER_ENTRY = 'browser'


@dataclass(frozen=True, slots=True)
class NewAccount:
    """What the form collected."""

    kind: ProviderKind
    variant: ProviderVariant
    auth: AuthMethod
    alias: str
    api_key: str = ''
    """Empty when ``auth`` is ``OAUTH``."""


class AccountFormScreen(ProviderModal):
    """Collects everything needed to add one account."""

    TITLE_TEXT = 'Add provider'
    HINT_TEXT = '↑/↓ choose · Enter continue · Esc cancel'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'back', 'Back', show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._step: Step = 'provider'
        self._kind: ProviderKind | None = None
        self._variant = ProviderVariant.DEFAULT
        self._auth = AuthMethod.API_KEY
        self._alias = ''

    def compose_body(self) -> ComposeResult:
        yield OptionList(id='form-options')
        yield Input(placeholder='Alias for this account…', id='form-alias')
        yield Input(placeholder='Paste the API key…', password=True, id='form-secret')
        yield Static('', id='form-status')

    def on_mount(self) -> None:
        self.query_one('#form-alias', Input).display = False
        self.query_one('#form-secret', Input).display = False
        self._render_step()

    # --- rendering ---------------------------------------------------------

    def _render_step(self) -> None:
        self.set_title(self._title())
        options = self.query_one(OptionList)
        options.clear_options()
        self._hide_inputs()

        if self._step == 'provider':
            self._render_options(options, [(kind.label, kind.value) for kind in ProviderKind])
            self.set_hint('↑/↓ choose · Enter continue · Esc cancel')
        elif self._step == 'variant':
            choices = [(variant.label, variant.value) for variant in variants_for(self._kind)]
            self._render_options(options, choices)
            self.set_hint('↑/↓ choose the product · Esc back')
        elif self._step == 'auth':
            choices = [(auth.label, auth.value) for auth in auth_methods_for(self._kind)]
            self._render_options(options, choices)
            self.set_hint('↑/↓ choose how to sign in · Esc back')
        elif self._step == 'alias':
            self._show_input('#form-alias')
            self.set_hint('Type the alias · Enter continue · Esc back')
        elif self._step == 'secret':
            self._show_input('#form-secret')
            self.set_hint('Paste the key · Enter save · Esc back')

    def _render_options(self, options: OptionList, choices: list[tuple[str, str]]) -> None:
        options.display = True
        for label, value in choices:
            options.add_option(Option(f'[bold]{label}[/bold]', id=value))
        options.highlighted = 0
        options.focus()

    def _hide_inputs(self) -> None:
        self.query_one('#form-alias', Input).display = False
        self.query_one('#form-secret', Input).display = False
        self.query_one(OptionList).display = False

    def _show_input(self, selector: str) -> None:
        self.query_one(OptionList).display = False
        field = self.query_one(selector, Input)
        field.display = True
        field.focus()

    def _title(self) -> str:
        if self._kind is None:
            return 'Add provider'
        name = self._kind.label
        if self._variant is not ProviderVariant.DEFAULT:
            name = f'{name} {self._variant.label}'
        if self._step == 'alias':
            return f'{name} — alias'
        if self._step == 'secret':
            return f'{name} — API key'
        return f'Add {name}' if self._step != 'provider' else 'Add provider'

    # --- transitions -------------------------------------------------------

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        value = str(event.option.id)
        if self._step == 'provider':
            self._kind = ProviderKind(value)
            self._advance_to_variant_or_auth()
        elif self._step == 'variant':
            self._variant = ProviderVariant(value)
            self._advance_to_auth_or_alias()
        elif self._step == 'auth':
            self._auth = AuthMethod(value)
            self._step = 'alias'
            self._render_step()

    def _advance_to_variant_or_auth(self) -> None:
        if len(variants_for(self._kind)) > 1:
            self._step = 'variant'
        else:
            self._variant = variants_for(self._kind)[0]
            self._advance_to_auth_or_alias()
            return
        self._render_step()

    def _advance_to_auth_or_alias(self) -> None:
        if len(auth_methods_for(self._kind)) > 1:
            self._step = 'auth'
            self._render_step()
            return
        self._auth = auth_methods_for(self._kind)[0]
        self._step = 'alias'
        self._render_step()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == 'form-alias':
            self._alias = event.value.strip()
            if not self._alias:
                self._status('The alias cannot be empty.', error=True)
                return
            if self._auth is AuthMethod.OAUTH:
                # Browser sign-in needs no key: hand the value object back now.
                self.dismiss(self._build(api_key=''))
                return
            self._step = 'secret'
            self._render_step()
            return

        if event.input.id == 'form-secret':
            api_key = event.value.strip()
            if not api_key:
                self._status('The key cannot be empty.', error=True)
                return
            self.dismiss(self._build(api_key=api_key))

    def _build(self, *, api_key: str) -> NewAccount:
        return NewAccount(
            kind=self._kind,
            variant=self._variant,
            auth=self._auth,
            alias=self._alias,
            api_key=api_key,
        )

    def _status(self, text: str, *, error: bool = False) -> None:
        style = 'bold red' if error else 'dim'
        self.query_one('#form-status', Static).update(f'[{style}]{text}[/{style}]')

    # --- cancellation ------------------------------------------------------

    def action_back(self) -> None:
        """`Esc` steps back; at the first step it closes the form."""
        previous = {
            'provider': None,
            'variant': 'provider',
            'auth': 'provider',
            'alias': 'auth' if len(auth_methods_for(self._kind)) > 1 else 'provider',
            'secret': 'alias',
        }[self._step]
        if previous is None:
            self.dismiss(None)
            return
        self._step = previous
        self._render_step()
