"""The normalize_alias helper of the providers layer."""

from __future__ import annotations

import re

from src.core.providers.domain.alias_error import AliasError

ALIAS_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 _.-]{0,31}$')


ALIAS_MAX_LENGTH = 32


def normalize_alias(raw: str) -> str:
    """Validates an alias and collapses its inner whitespace.

    Raises:
        AliasError: When it is empty, too long, or has a forbidden character.
    """
    alias = ' '.join(raw.split())
    if not alias:
        raise AliasError('The alias cannot be empty.')
    if len(alias) > ALIAS_MAX_LENGTH:
        raise AliasError(f'The alias can be at most {ALIAS_MAX_LENGTH} characters.')
    if not ALIAS_PATTERN.match(alias):
        raise AliasError(
            'The alias may only contain letters, digits, spaces, dots, dashes and underscores, '
            'and it must start with a letter or a digit.'
        )
    return alias
