"""Discovery and lifecycle of explicitly trusted local tool extensions."""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from sigil_tools import ExtensionContext, ToolDeclaration
from src.engine.paths import sigil_home


@dataclass(frozen=True, slots=True)
class ExtensionTool:
    name: str
    declaration: ToolDeclaration
    directory: Path
    project: bool


@dataclass(frozen=True, slots=True)
class _Loaded:
    module: ModuleType
    context: ExtensionContext
    tools: tuple[ExtensionTool, ...]


class ExtensionManager:
    """Loads extensions only after their source has been trusted by the caller."""

    def __init__(self, workspace: Path, *, global_dir: Path | None = None, trust_project: bool = False) -> None:
        self.workspace = workspace.resolve()
        self.global_dir = global_dir if global_dir is not None else sigil_home() / 'tools'
        self.trust_project = trust_project
        self.errors: list[str] = []
        self.tools: tuple[ExtensionTool, ...] = ()
        self._loaded: list[_Loaded] = []
        self.registered_declarations: dict[str, object] = {}
        self.hook_timeout = 10.0

    async def load(self) -> None:
        if self._loaded:
            raise RuntimeError('Extensions are already loaded')
        candidates = [(path, False) for path in self._directories(self.global_dir)]
        project_dir = self.workspace / '.sigil' / 'tools'
        project = [] if project_dir.resolve() == self.global_dir.resolve() else self._directories(project_dir)
        if project and not self.trust_project:
            self.errors.append(f'Project extensions in {project_dir} require trust before loading.')
        elif self.trust_project:
            candidates.extend((path, True) for path in project)
        try:
            for directory, is_project in candidates:
                item = await self._load_extension(directory, is_project)
                if item is not None:
                    self._loaded.append(item)
            self.tools = self._select_tools(self._loaded)
        except BaseException:
            await self.close()
            raise

    @staticmethod
    def _directories(root: Path) -> list[Path]:
        if not root.is_dir():
            return []
        return sorted(entry for entry in root.iterdir() if entry.is_dir() and (entry / 'tools.py').is_file())

    async def _load_extension(self, directory: Path, project: bool) -> _Loaded | None:
        module_name = f'_sigil_extension_{uuid.uuid4().hex}'
        context = ExtensionContext(extension_dir=directory, workspace=self.workspace)
        module: ModuleType | None = None
        try:
            spec = importlib.util.spec_from_file_location(
                module_name, directory / 'tools.py', submodule_search_locations=[str(directory)]
            )
            if spec is None or spec.loader is None:
                raise ImportError(f'Cannot load {directory / "tools.py"}')
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            tools = self._declarations(module, directory, project)
            for name in ('on_load', 'on_unload'):
                hook = getattr(module, name, None)
                if hook is not None:
                    if not inspect.iscoroutinefunction(hook):
                        raise TypeError(f'{name} must be declared with async def')
                    inspect.signature(hook).bind(context)
                    if getattr(hook, '__sigil_tool__', None) is not None:
                        raise TypeError(f'{name} is a reserved lifecycle hook, not a tool')
            hook = getattr(module, 'on_load', None)
            if hook is not None:
                await asyncio.wait_for(hook(context), timeout=self.hook_timeout)
            return _Loaded(module, context, tools)
        except (Exception, asyncio.CancelledError) as exc:
            if not isinstance(exc, asyncio.CancelledError):
                self.errors.append(f'{directory}: {type(exc).__name__}: {exc}')
            if module is not None:
                hook = getattr(module, 'on_unload', None)
                if inspect.iscoroutinefunction(hook):
                    try:
                        await asyncio.wait_for(hook(context), timeout=self.hook_timeout)
                    except (Exception, asyncio.CancelledError) as cleanup:  # noqa: BLE001 - cleanup boundary
                        self.errors.append(f'{directory}: cleanup failed: {type(cleanup).__name__}: {cleanup}')
            self._forget(module_name)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return None

    @staticmethod
    def _declarations(module: ModuleType, directory: Path, project: bool) -> tuple[ExtensionTool, ...]:
        tools: list[ExtensionTool] = []
        seen: set[int] = set()
        for value in vars(module).values():
            declaration = getattr(value, '__sigil_tool__', None)
            if not isinstance(declaration, ToolDeclaration) or id(value) in seen:
                continue
            seen.add(id(value))
            tools.append(ExtensionTool(declaration.name or value.__name__, declaration, directory, project))
        return tuple(tools)

    def _select_tools(self, loaded: list[_Loaded]) -> tuple[ExtensionTool, ...]:
        selected: dict[str, ExtensionTool] = {}
        duplicates: set[tuple[str, bool]] = set()
        for item in loaded:
            for tool in item.tools:
                existing = selected.get(tool.name)
                if existing is not None and existing.project == tool.project:
                    duplicates.add((tool.name, tool.project))
                    self.errors.append(f'Duplicate tool {tool.name!r}: {existing.directory} and {tool.directory}')
                if existing is None or tool.project:
                    if existing is not None and tool.project and not existing.project:
                        self.errors.append(f'Project tool {tool.name!r} overrides {existing.directory}')
                    selected[tool.name] = tool
        return tuple(tool for tool in selected.values() if (tool.name, tool.project) not in duplicates)

    async def close(self) -> None:
        self.tools = ()
        for item in reversed(self._loaded):
            hook = getattr(item.module, 'on_unload', None)
            try:
                if hook is not None:
                    await asyncio.wait_for(hook(item.context), timeout=self.hook_timeout)
            except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - cleanup must continue
                self.errors.append(f'{item.context.extension_dir}: unload failed: {type(exc).__name__}: {exc}')
            finally:
                self._forget(item.module.__name__)
        self._loaded.clear()

    @staticmethod
    def _forget(module_name: str) -> None:
        for name in tuple(sys.modules):
            if name == module_name or name.startswith(module_name + '.'):
                del sys.modules[name]
