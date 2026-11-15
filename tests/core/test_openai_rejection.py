"""What a rejected credential turns into, and what it must never turn into.

The failure this covers: the catalogue request comes back 401, httpx raises, and the
service reports `str(exc)` — which is the request line plus "For more information check
out: https://developer.mozilla.org/..." on four lines. That is the message a person
reads next to the model picker, and it says nothing about what to do.

A 401 on a plan account is an expired session; on an API-key account it is a bad key.
Different cause, different sentence.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.engine.providers.catalog.catalog_service import ModelCatalogService, _describe
from src.engine.providers.catalog.openai_source import OpenAISource
from src.engine.providers.domain.auth_method import AuthMethod
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.provider_variant import ProviderVariant


def _account(auth: AuthMethod) -> ProviderAccount:
    return ProviderAccount.create('acct', ProviderKind.OPENAI, ProviderVariant.DEFAULT, auth=auth)


class _StatusError(Exception):
    """What httpx raises, in the only shape the source reads: `.response.status_code`."""

    def __init__(self, status: int) -> None:
        super().__init__(
            f"Client error '{status} Unauthorized' for url 'https://api.openai.com/v1/models'\n"
            'For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401'
        )
        self.response = type('Response', (), {'status_code': status})()


def _service_where(status: int) -> ModelCatalogService:
    """A catalogue service whose HTTP call always answers ``status``."""
    service = ModelCatalogService(sources=(OpenAISource(profile_lookup=lambda _name: None),))

    async def fetch(url: str, headers: dict[str, str], timeout: float) -> Any:
        raise _StatusError(status)

    service._fetch_json = fetch
    return service


@pytest.mark.parametrize('status', [401, 403])
def test_a_refused_plan_token_names_the_action(status: int) -> None:
    catalog = asyncio.run(_service_where(status).models(_account(AuthMethod.OAUTH), secret='expired'))

    assert catalog.source == 'static'
    assert catalog.error is not None
    assert 'Sign in again with /provider' in catalog.error
    assert 'developer.mozilla.org' not in catalog.error


def test_a_refused_api_key_names_the_key() -> None:
    catalog = asyncio.run(_service_where(401).models(_account(AuthMethod.API_KEY), secret='wrong'))

    assert catalog.error is not None
    assert 'API key' in catalog.error


def test_a_transport_failure_keeps_its_first_line_only() -> None:
    """The picker shows one line; the MDN link is not it."""
    described = _describe(_StatusError(500))
    assert described == "Client error '500 Unauthorized' for url 'https://api.openai.com/v1/models'"
    assert '\n' not in described


def test_an_empty_exception_still_has_a_name() -> None:
    assert _describe(ValueError()) == 'ValueError'
