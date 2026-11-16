"""The ToolPermission value of the tooling layer."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from src.core.tooling.resource_kind import ResourceKind

PERMISSION_METADATA_KEY = 'sigil_permission'


@dataclass(frozen=True, slots=True)
class ToolPermission:
    """What a tool is, for the permission filter of the active profile."""

    groups: tuple[str, ...]
    """Coarse categories the tool belongs to; matched as exact names."""

    resource: str | None = None
    """Argument whose value the resource patterns are matched against."""

    kind: ResourceKind = ResourceKind.TEXT
    """How that value is normalized before matching."""

    resolve_resource: Callable[[Mapping[str, Any]], tuple[str, ...]] | None = None
    """Resolve resources that cannot be trusted directly from arguments."""

    def metadata(self) -> dict[str, Any]:
        """The declaration as tool metadata."""
        return {
            PERMISSION_METADATA_KEY: {
                'groups': [str(group) for group in self.groups],
                'resource': self.resource,
                'kind': str(self.kind),
                'resolve_resource': self.resolve_resource,
            }
        }
