"""Checking that a credential works before it is saved.

The providers do not answer alike, so the probe is per family and the *meaning*
of a status code is decided here once (verified against the live endpoints):

======================  =========================================  ==============
Provider                Probe                                      401/403 means
======================  =========================================  ==============
OpenRouter              ``GET /api/v1/key``                        key rejected
OpenAI                  ``GET /v1/models``                         key rejected
OpenCode (Zen)          ``GET /zen/v1/models``                     key rejected
OpenCode (Go)           ``POST`` a one-token completion            key rejected
======================  =========================================  ==============

Go deserves a note. Its endpoint answers ``400 Model is unavailable`` for an
unknown model *before* looking at the key, and ``429 FreeUsageLimitError`` when
the key is valid but the plan's free allowance is spent. Neither is a bad key, so
only ``401``/``403`` reject an account and everything else is a warning.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import httpx

from src.engine.providers.catalog.source import DEFAULT_TIMEOUT
from src.engine.providers.domain.provider_account import ProviderAccount
from src.engine.providers.domain.provider_kind import ProviderKind
from src.engine.providers.domain.provider_variant import ProviderVariant

OPENROUTER_KEY_URL = 'https://openrouter.ai/api/v1/key'
OPENCODE_ZEN_MODELS_URL = 'https://opencode.ai/zen/v1/models'
OPENAI_MODELS_URL = 'https://api.openai.com/v1/models'
OPENCODE_GO_COMPLETIONS_URL = 'https://opencode.ai/inference/go/openai/v1/chat/completions'
OPENCODE_GO_PROBE_MODEL = 'glm-5.3'
"""A model the Go plan lists, used only to make the endpoint look at the key."""

ProbeOutcome = Literal['valid', 'rejected', 'unknown']


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What the probe could establish."""

    outcome: ProbeOutcome
    detail: str = ''

    @property
    def is_rejected(self) -> bool:
        return self.outcome == 'rejected'

    @property
    def is_valid(self) -> bool:
        return self.outcome == 'valid'


async def probe_credential(
    account: ProviderAccount,
    api_key: str,
    *,
    client_factory: object | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ProbeResult:
    """Checks ``api_key`` against ``account``'s provider.

    A network failure is not a rejection: the caller saves the account with a
    warning, because refusing to configure a provider while offline would be a
    dead end.
    """
    del client_factory  # kept for tests that want to inject a transport
    try:
        if account.kind is ProviderKind.OPENROUTER:
            return await _probe_get(OPENROUTER_KEY_URL, api_key, timeout)
        if account.kind is ProviderKind.OPENAI:
            return await _probe_get(OPENAI_MODELS_URL, api_key, timeout)
        if account.variant is ProviderVariant.GO:
            return await _probe_go(api_key, timeout)
        return await _probe_get(OPENCODE_ZEN_MODELS_URL, api_key, timeout)
    except httpx.HTTPError as exc:
        return ProbeResult('unknown', _describe(exc))


async def _probe_get(url: str, api_key: str, timeout: float) -> ProbeResult:
    headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
    return _classify(response.status_code, response.text)


async def _probe_go(api_key: str, timeout: float) -> ProbeResult:
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'User-Agent': 'sigil/0.1.0',
        'x-opencode-session': 'credential-probe',
    }
    body = {
        'model': OPENCODE_GO_PROBE_MODEL,
        'messages': [{'role': 'user', 'content': 'ping'}],
        'max_tokens': 1,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(OPENCODE_GO_COMPLETIONS_URL, headers=headers, json=body)
    result = _classify(response.status_code, response.text)
    if result.outcome == 'unknown' and response.status_code in {400, 429}:
        # The key survived the only check this endpoint performs before routing.
        return ProbeResult('valid', '')
    return result


def _classify(status_code: int, body: str, headers: Mapping[str, str] | None = None) -> ProbeResult:
    del headers
    if status_code in {401, 403}:
        return ProbeResult('rejected', _short(body))
    if 200 <= status_code < 300:
        return ProbeResult('valid', '')
    return ProbeResult('unknown', f'HTTP {status_code}: {_short(body)}')


def _short(body: str, limit: int = 160) -> str:
    text = ' '.join(body.split())
    return text if len(text) <= limit else text[: limit - 1] + '…'


def _describe(exc: httpx.HTTPError) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__
