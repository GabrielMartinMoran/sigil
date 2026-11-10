"""Validation of agent profile identifiers and references."""

import re

_NAME_RE = re.compile(r'[a-z0-9]+(?:[-_][a-z0-9]+)*')


def is_valid_agent_name(name: str) -> bool:
    return _NAME_RE.fullmatch(name) is not None
