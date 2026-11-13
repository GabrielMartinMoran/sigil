"""Writing the model defaults, and reading them back per file.

`agents.<name>` and `default_model` were read-only for a long time: the file was
the interface, and setting a default meant opening an editor. The TUI writes them
now, which turns this module into an owner of two directions. Two rules keep that
honest:

- **A write touches its own entry.** The MCP servers, the other agents and the keys
  this version does not know go back to disk with the same values. A key from a
  later version is not ours to delete, and a file is not ours to lose. What does
  change is the layout: `write_json` writes `indent=2` with a trailing newline, the
  shape every other file Sigil owns has.
- **A file that does not parse is not rewritten.** Reporting it is the only safe
  answer: guessing what the user meant would cost them the rest of the file.

The state is read per file, not merged, because the screen has to say *which* file
an entry comes from — the merge itself stays in `loader.load_config`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from src.engine.agents.model_config import AgentModelConfig, parse_agent_models, parse_default_model
from src.engine.mcp.config.constants import ENV_CONFIG
from src.engine.mcp.config.paths import config_paths
from src.engine.providers.store.atomic import write_json

AGENTS_KEY = 'agents'
DEFAULT_KEY = 'default_model'


class ConfigScope(StrEnum):
    """Which of the two files an entry belongs to.

    ``PROJECT`` wins over ``GLOBAL``: the order here is the precedence order, and
    the state below relies on it instead of restating it.
    """

    PROJECT = 'project'
    GLOBAL = 'global'


SCOPES: tuple[ConfigScope, ...] = (ConfigScope.PROJECT, ConfigScope.GLOBAL)


def defaults_paths(cwd: str | os.PathLike[str] | None = None) -> tuple[Path, Path]:
    """The project and the user config, or the same explicit file twice.

    ``SIGIL_CONFIG`` replaces both files everywhere else, so it does here too: the
    pair collapses into one path and the screen offers a single destination.
    """
    fixed = _fixed_path()
    if fixed is not None:
        return fixed, fixed
    project, user = config_paths(cwd=cwd)
    return project, user


def _fixed_path() -> Path | None:
    override = os.environ.get(ENV_CONFIG, '').strip()
    return Path(override).expanduser() if override else None


@dataclass(frozen=True, slots=True)
class ConfigFileState:
    """What one config file says about models, and what is wrong with it."""

    path: Path
    default: AgentModelConfig | None = None
    default_invalid: bool = False
    agents: Mapping[str, AgentModelConfig] = field(default_factory=dict)
    invalid_agents: frozenset[str] = frozenset()
    errors: tuple[str, ...] = ()

    def mentions(self, target: str | None) -> bool:
        """Whether this file says anything about ``target``, validly or not.

        It matters that an invalid entry still counts as a mention: a project file
        that sets an agent badly shadows the user's file instead of falling back to
        it, and the screen has to show that rather than a value the merge ignored.
        """
        if target is None:
            return self.default is not None or self.default_invalid
        return target in self.agents or target in self.invalid_agents

    def entry(self, target: str | None) -> AgentModelConfig | None:
        return self.default if target is None else self.agents.get(target)

    def is_invalid(self, target: str | None) -> bool:
        return self.default_invalid if target is None else target in self.invalid_agents


@dataclass(frozen=True, slots=True)
class ModelDefaultsState:
    """Both config files, in precedence order, as the screen needs them."""

    files: Mapping[ConfigScope, ConfigFileState]
    fixed: Path | None = None
    """The single explicit path, when ``SIGIL_CONFIG`` replaced the pair."""

    @property
    def destinations(self) -> tuple[ConfigScope, ...]:
        return tuple(scope for scope in SCOPES if scope in self.files)

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(error for scope in self.destinations for error in self.files[scope].errors)

    def path_for(self, scope: ConfigScope) -> Path:
        return self.files[scope].path

    def sources(self, target: str | None) -> frozenset[ConfigScope]:
        """The files that mention ``target``. One reading is enough for the screen."""
        return frozenset(scope for scope in self.destinations if self.files[scope].mentions(target))

    def effective(self, target: str | None) -> AgentModelConfig | None:
        """The entry that wins, or ``None`` when no file sets one."""
        for scope in self.destinations:
            file = self.files[scope]
            if file.mentions(target):
                return file.entry(target)
        return None

    def invalid(self, target: str | None) -> bool:
        """Whether the winning entry is one the files could not parse."""
        for scope in self.destinations:
            file = self.files[scope]
            if file.mentions(target):
                return file.is_invalid(target)
        return False

    def agent_names(self) -> tuple[str, ...]:
        """Every agent either file mentions, including the badly written ones."""
        names = set()
        for scope in self.destinations:
            file = self.files[scope]
            names.update(file.agents)
            names.update(file.invalid_agents)
        return tuple(sorted(names))


def read_defaults(
    *,
    paths: tuple[Path, Path] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> ModelDefaultsState:
    """Reads both config files without merging them."""
    project, user = paths if paths is not None else defaults_paths(cwd)
    fixed = project if project == user else None
    if fixed is not None:
        return ModelDefaultsState({ConfigScope.GLOBAL: _file_state(fixed)}, fixed=fixed)
    return ModelDefaultsState({ConfigScope.PROJECT: _file_state(project), ConfigScope.GLOBAL: _file_state(user)})


def save_default(
    target: str | None,
    config: AgentModelConfig | None,
    scope: ConfigScope,
    *,
    paths: tuple[Path, Path] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> tuple[str, ...]:
    """Sets (``config``) or clears (``None``) one entry, in one file.

    Args:
        target: An agent name, or ``None`` for the block a conversation starts on.
        config: The entry to write, or ``None`` to remove the one that is there.
        scope: Which file to write.
        paths: Explicit pair, for tests. Discovered from ``cwd`` by default.

    Returns:
        The errors that stopped the write, empty when it happened.
    """
    project, user = paths if paths is not None else defaults_paths(cwd)
    path = project if scope is ConfigScope.PROJECT else user
    root, errors = _read_root(path)
    if errors:
        return errors
    assert root is not None
    errors, changed = _apply(root, target, config)
    if errors or not changed:
        # Nothing to write is not a failure: clearing an entry that is not there
        # must not create an empty config file in a repository.
        return errors
    try:
        write_json(path, root)
    except OSError as exc:
        return (f'{path}: cannot write config: {exc}',)
    return ()


def _apply(root: dict, target: str | None, config: AgentModelConfig | None) -> tuple[tuple[str, ...], bool]:
    """Puts one entry into ``root``, in place.

    Returns ``(errors, changed)``: a write that would not change anything is skipped,
    so clearing an entry that does not exist does not create the file.
    """
    if target is None:
        if config is None:
            return (), root.pop(DEFAULT_KEY, None) is not None
        if root.get(DEFAULT_KEY) == _block(config):
            return (), False
        root[DEFAULT_KEY] = _block(config)
        return (), True

    section = root.get(AGENTS_KEY, {})
    if not isinstance(section, dict):
        # Replacing it would silently drop whatever the user wrote there.
        return (f'"{AGENTS_KEY}" is not an object, so it cannot hold "{target}".',), False
    agents = dict(section)
    if config is None:
        if agents.pop(target, None) is None:
            return (), False
    elif agents.get(target) == _block(config):
        return (), False
    else:
        agents[target] = _block(config)
    if agents:
        root[AGENTS_KEY] = agents
    else:
        root.pop(AGENTS_KEY, None)
    return (), True


def _block(config: AgentModelConfig) -> dict[str, str]:
    """One entry, with the three keys in the order a reader expects."""
    return {'account': config.account, 'model': config.model, 'effort': config.effort}


def _file_state(path: Path) -> ConfigFileState:
    """One file, parsed with the same rules the loader uses at startup."""
    root, errors = _read_root(path)
    if root is None:
        return ConfigFileState(path=path, errors=errors)

    agents, invalid_agents, agent_errors = parse_agent_models(root.get(AGENTS_KEY, {}), path)
    default, default_errors = parse_default_model(root.get(DEFAULT_KEY), path)
    return ConfigFileState(
        path=path,
        default=default,
        # `parse_default_model` answers `None` for both "absent" and "wrong"; only the
        # second is worth flagging, and the raw value is what tells them apart.
        default_invalid=root.get(DEFAULT_KEY) is not None and default is None,
        agents=agents,
        invalid_agents=frozenset(invalid_agents),
        errors=tuple(agent_errors + default_errors),
    )


def _read_root(path: Path) -> tuple[dict | None, tuple[str, ...]]:
    """The file's object, an empty one when it does not exist, or why not.

    A missing file is not an error: the first write creates it, and until then the
    screen has a state to show with nothing in it.
    """
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}, ()
    except OSError as exc:
        return None, (f'{path}: cannot read config: {exc}',)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, (f'{path}: cannot read config: {exc}',)
    if not isinstance(data, dict):
        return None, (f'{path}: the config root must be a JSON object.',)
    return data, ()


__all__ = [
    'SCOPES',
    'ConfigFileState',
    'ConfigScope',
    'ModelDefaultsState',
    'defaults_paths',
    'read_defaults',
    'save_default',
]
