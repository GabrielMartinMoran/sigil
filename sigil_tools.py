"""Public declarations for local Sigil tool extensions."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.engine.tooling.compaction import ToolCompactor, compact_json, compact_text
from src.engine.tooling.concurrency import ConcurrencyContext, ConcurrencyGuard
from src.engine.tooling.outputs import OutputPresenter, ToolOutput
from src.engine.tooling.permission import ResourceKind, ToolGroup, ToolPermission
from src.engine.tooling.presentation import Describe, ToolSummary


@dataclass(frozen=True, slots=True)
class ExtensionContext:
    extension_dir: Path
    workspace: Path


@dataclass(frozen=True, slots=True)
class ToolContext:
    workspace: Path
    agent_id: str
    agent_name: str
    session_id: str


@dataclass(frozen=True, slots=True)
class ToolDeclaration:
    function: Callable[..., Any]
    name: str | None = None
    description: str | None = None
    permission: ToolPermission | None = None
    compaction: ToolCompactor | None = None
    concurrency: ConcurrencyGuard | None = None
    describe: Describe | None = None
    output: OutputPresenter | None = None
    requires: str | tuple[str, ...] | None = None


def tool[F: Callable[..., Any]](
    func: F | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    permission: ToolPermission | None = None,
    compaction: ToolCompactor | None = None,
    concurrency: ConcurrencyGuard | None = None,
    describe: Describe | None = None,
    output: OutputPresenter | None = None,
    requires: str | tuple[str, ...] | None = None,
) -> Any:
    """Mark an asynchronous function as a local tool."""

    def decorate(target: F) -> F:
        if not inspect.iscoroutinefunction(target):
            raise TypeError(f'Tool {target.__name__!r} must be declared with async def')
        callbacks: dict[str, Callable[..., object] | None] = {
            'concurrency': concurrency,
            'describe': describe,
            'output': output,
        }
        if permission is not None:
            callbacks['permission.resolve_resource'] = permission.resolve_resource
        if compaction is not None:
            callbacks.update({'compaction.input': compaction.input, 'compaction.output': compaction.output})
        for label, callback in callbacks.items():
            if callback is not None and inspect.iscoroutinefunction(callback):
                raise TypeError(f'{label} must be synchronous')
        declaration = ToolDeclaration(
            target, name, description, permission, compaction, concurrency, describe, output, requires
        )
        target.__dict__['__sigil_tool__'] = declaration
        return target

    return decorate(func) if func is not None else decorate


__all__ = [
    'ConcurrencyContext',
    'ConcurrencyGuard',
    'Describe',
    'ExtensionContext',
    'OutputPresenter',
    'ResourceKind',
    'ToolCompactor',
    'ToolContext',
    'ToolDeclaration',
    'ToolGroup',
    'ToolOutput',
    'ToolPermission',
    'ToolSummary',
    'compact_json',
    'compact_text',
    'tool',
]
