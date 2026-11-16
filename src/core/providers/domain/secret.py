"""Credential material, in a shape that cannot leak by accident.

Two rules, both enforced by the type rather than by discipline:

1. ``repr`` and ``str`` never contain the secret. A traceback, a log line or a
   ``f'{credential}'`` shows ``ApiKeySecret(alias=work)``.
2. A builder receives the *narrow* variant it needs, so it cannot read a field
   that belongs to another auth method.
"""

from __future__ import annotations

from src.core.providers.domain.api_key_secret import ApiKeySecret
from src.core.providers.domain.o_auth_secret import OAuthSecret

SecretMaterial = ApiKeySecret | OAuthSecret
"""What the store hands to a builder."""
