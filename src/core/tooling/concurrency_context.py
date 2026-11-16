"""The ConcurrencyContext value of the tooling layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.tooling.sibling_call import SiblingCall


@dataclass(frozen=True, slots=True)
class ConcurrencyContext:
    """What a guard sees when deciding whether the call can run in parallel."""

    tool_name: str
    """Name of the tool that is about to run."""

    args: dict[str, Any]
    """Effective args of the call, with the defaults applied."""

    tool_call_id: str | None
    """Id of the current call, if the model or the framework assigned one."""

    siblings: tuple[SiblingCall, ...]
    """Calls emitted before this one in the same response, in order."""
