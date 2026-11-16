"""One action the provider screen offers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Action = Literal['add', 'delete', 'rename', 'activate']


@dataclass(frozen=True, slots=True)
class ProviderAction:
    """What the user chose in the list."""

    action: Action
    alias: str = ''
