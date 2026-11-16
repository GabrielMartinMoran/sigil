"""OpenAI accounts: API key and ChatGPT-plan sign-in.

The two auth methods end at the same place — ``OpenAIResponsesModel`` — but with
different providers:

- API key → ``OpenAIProvider``. Plain bearer token.
- OAuth → ``OpenAIChatGPTProvider``. It owns the token lifecycle (expiry check,
  refresh, single-flight after a 401) and persists rotated tokens through the
  credential source we hand it.

The Responses API is used in both cases because it carries reasoning natively,
which is what the agent needs to reason across turns.
"""

from __future__ import annotations

"""Builds the OAuth-backed provider for an account.

Injected so this module does not import the credential store, and so a test can
substitute a provider that never talks to the network.
"""
