"""Which tools an agent may use, and on what.

The frontmatter of a profile declares one or more **rules**, in order:

```yaml
permissions:
  all: deny                    # everything off...
  read: allow                  # ...except reading
  run:                         # ...and the shell, but only these commands
    allow: ['git status *', 'git diff *']
    deny: ['git push *']
```

A rule has a **scope** — ``all``, a group (``read``, ``write``, ``run``…), a tool
name (``patch_file``) or a glob over names (``mcp_github_*``) — an **effect**
(``allow`` or ``deny``) and, optionally, **patterns** over the resource the tool
acts on: a path for ``read``/``write``, a command for ``run``, a query or a URL
for ``web``.

Two decisions come out of it, and they are deliberately not the same:

- **Is the tool advertised?** A tool whose last matching rule is a whole-tool
  ``deny`` never reaches the model. It is not refused at call time: the model
  does not know it exists and cannot plan around it.
- **May this call run?** A rule with patterns keeps the tool visible and rules on
  each call instead. That is the only way to say "the shell, but not ``rm``"
  without losing the shell.

The rules are compared in order and the last match wins, so what a profile reads
top to bottom is what happens: broad first, specific after.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from src.engine.agents.profile.models.agent_parse_error import AgentParseError
from src.engine.files.paths import workspace_root
from src.engine.tooling.permission import ALL_SCOPES, ResourceKind, scope_is_known, scope_problem

_HOME_PREFIXES = ('~/', '$HOME/', '${HOME}/')
_WHITESPACE = re.compile(r'\s+')


class Effect(StrEnum):
    """What a rule does when it matches."""

    ALLOW = 'allow'
    """The call (or the tool) is permitted."""

    DENY = 'deny'
    """The call (or the tool) is refused."""


@dataclass(frozen=True, slots=True)
class Rule:
    """One line of the policy: a scope, an effect and maybe resource patterns."""

    scope: str
    """``*``, a group, a tool name, or a glob over names."""

    effect: Effect
    """What happens when the scope (and the patterns) match."""

    patterns: tuple[str, ...] = ()
    """Resource patterns. Empty means the rule is about the whole tool."""

    def matches_tool(self, tool_name: str, groups: tuple[str, ...]) -> bool:
        """Whether the scope covers this tool, by name, group or wildcard."""
        if self.scope == '*':
            return True
        if self.scope in groups:
            return True
        return fnmatchcase(tool_name, self.scope)

    def matches_resource(self, values: tuple[str, ...], kind: ResourceKind) -> bool:
        """Whether the call acts on something the rule talks about.

        A rule without patterns speaks about every resource. Otherwise *one* of
        the values has to match: ``run`` executes several commands and a single
        denied one is enough to refuse the call.
        """
        if not self.patterns:
            return True
        return any(_matches(pattern, value, kind) for pattern in self.patterns for value in values)


@dataclass(frozen=True, slots=True)
class ToolPermissions:
    """The ordered rules a profile declares."""

    rules: tuple[Rule, ...] = ()
    """In file order; the last matching rule decides."""

    @property
    def restricted(self) -> bool:
        """Whether the profile declared anything at all.

        An empty rule list is not "nothing is allowed": it is "the file did not
        say", which leaves every tool in place.
        """
        return bool(self.rules)

    # --- the two decisions -------------------------------------------------

    def advertises(self, tool_name: str, groups: tuple[str, ...] = ()) -> bool:
        """Whether ``tool_name`` may be offered to the model at all."""
        if not self.rules:
            return True
        matching = [rule for rule in self.rules if rule.matches_tool(tool_name, groups)]
        if not matching:
            return True
        last = matching[-1]
        # A rule with patterns re-opens the tool: it rules on the call, not on
        # whether the tool exists.
        return last.effect is Effect.ALLOW or bool(last.patterns)

    def permits(
        self,
        tool_name: str,
        groups: tuple[str, ...] = (),
        resources: tuple[str, ...] = (),
        kind: ResourceKind = ResourceKind.TEXT,
    ) -> bool:
        """Whether this concrete call may run.

        The last matching rule wins. A narrowed rule decides only the calls whose
        resource it names, and the effect it carries says what the narrowing
        means:

        - an **allow list** (`allow: ['git status *']`) is exhaustive: a call it
          does not name is refused, or the list would only be decoration;
        - a **deny list** (`deny: ['rm *']`) subtracts: every other call of the
          tool is fine.

        With both, the allow list bounds the tool and the deny list removes from
        it, in the order they are written.
        """
        if not self.rules:
            return True
        decision: bool | None = None
        allow_listed = False
        for rule in self.rules:
            if not rule.matches_tool(tool_name, groups):
                continue
            if rule.patterns:
                allow_listed = allow_listed or rule.effect is Effect.ALLOW
                if not rule.matches_resource(resources, kind):
                    continue
            decision = rule.effect is Effect.ALLOW
        if decision is not None:
            return decision
        return not allow_listed

    # --- the picker --------------------------------------------------------

    def describe(self) -> str:
        """One line for the picker: what the profile can and cannot touch."""
        if not self.rules:
            return 'all tools'
        granted = _unique(rule.scope for rule in self.rules if rule.effect is Effect.ALLOW and not rule.patterns)
        denied = _unique(rule.scope for rule in self.rules if rule.effect is Effect.DENY and not rule.patterns)
        narrowed = _unique(rule.scope for rule in self.rules if rule.patterns)
        if self._closes_everything():
            summary = f'only {", ".join(granted) or "nothing"}'
        elif denied:
            summary = f'all tools except {", ".join(denied)}'
        else:
            summary = 'all tools'
        if narrowed:
            summary += f' ({", ".join(narrowed)} by pattern)'
        return summary

    def _closes_everything(self) -> bool:
        """Whether the rules open from a closed baseline rather than restrict."""
        return bool(self.rules) and self.rules[0].scope == '*' and self.rules[0].effect is Effect.DENY


UNRESTRICTED = ToolPermissions()
"""What a profile that says nothing gets."""


# --- matching ----------------------------------------------------------------


def _matches(pattern: str, value: str, kind: ResourceKind | str) -> bool:
    try:
        resolved = ResourceKind(kind)
    except ValueError:
        resolved = ResourceKind.TEXT
    if resolved is ResourceKind.PATH:
        return _matches_path(pattern, value)
    if resolved is ResourceKind.COMMAND:
        return _matches_command(pattern, value)
    return fnmatchcase(value, pattern)


def _matches_command(pattern: str, command: str) -> bool:
    """Matches a command, with the ``git status *`` idiom for the bare form.

    A pattern ending in `` *`` also matches the command without arguments, so
    ``git status *`` covers both ``git status`` and ``git status --short``.
    Whitespace is collapsed on both sides: a rule is not a whitespace test.
    """
    normalized = _collapse(command)
    candidate = _collapse(pattern)
    if fnmatchcase(normalized, candidate):
        return True
    return candidate.endswith(' *') and fnmatchcase(normalized, candidate[:-2])


def _matches_path(pattern: str, value: str) -> bool:
    """Matches a path, relative to the workspace or absolute.

    The pattern can be written either way — ``src/*.py``, ``./src/*.py``,
    ``$HOME/notes/*`` — and the value is tried in both forms, so the rule does
    not depend on how the model spelled the path it asked for.
    """
    candidates = _path_candidates(value)
    for form in _pattern_forms(pattern):
        if any(fnmatchcase(candidate, form) for candidate in candidates):
            return True
    return False


def _path_candidates(value: str) -> tuple[str, ...]:
    absolute = _absolute(value)
    relative = _relative(absolute)
    return (absolute,) if relative is None else (relative, absolute)


def _pattern_forms(pattern: str) -> tuple[str, ...]:
    expanded = _expand_home(pattern)
    if os.path.isabs(expanded):
        return (_normalize(expanded),)
    normalized = _normalize(expanded)
    return (normalized, _normalize(str(workspace_root() / normalized)))


def _absolute(path: str) -> str:
    expanded = _expand_home(path)
    if not os.path.isabs(expanded):
        expanded = str(workspace_root() / expanded)
    return _normalize(expanded)


def _relative(absolute: str) -> str | None:
    try:
        return Path(absolute).relative_to(workspace_root()).as_posix()
    except ValueError:
        return None


def _expand_home(path: str) -> str:
    if path == '~' or path.startswith(_HOME_PREFIXES):
        return str(Path(os.path.expanduser(path.replace('${HOME}', '~').replace('$HOME', '~'))))
    return path


def _normalize(path: str) -> str:
    """Slash-separated, no ``..`` and no duplicate separators."""
    normalized = os.path.normpath(path).replace(os.sep, '/')
    return normalized


def _collapse(text: str) -> str:
    return _WHITESPACE.sub(' ', text).strip()


def _unique(values: Any) -> list[str]:
    """The scopes in the order they are written, once each."""
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(str(value), None)
    return list(seen)


# --- parsing -----------------------------------------------------------------

_EFFECTS = {str(effect): effect for effect in Effect}

_LEGACY_HINT = (
    '`permissions.allow` / `permissions.deny` are no longer accepted. '
    'Group the rules by tool instead: `permissions: {"all": deny, read: allow, run: {allow: ["git status *"]}}`.'
)


def _canonical_scope(scope: str) -> str:
    """``all`` and ``*`` are the same rule; both spellings reach it."""
    return '*' if scope in ALL_SCOPES else scope


def parse_permissions(raw: Any) -> ToolPermissions:
    """Builds the rules from the ``permissions`` key of the frontmatter.

    Three shapes, from loosest to strictest:

    ```yaml
    # (nothing)                          every tool, including MCP and the shell
    permissions: [read, write, ask]      # only these scopes; the rest is denied
    permissions:                         # the explicit, ordered form
      '*': deny
      read: allow
      run:
        allow: ['git status *']
        deny: ['rm *']
    ```

    Raises:
        AgentParseError: When the value has a shape the profile does not accept.
    """
    if raw is None:
        return UNRESTRICTED

    if isinstance(raw, str):
        raise AgentParseError(
            '`permissions` must be a list of scopes or a mapping of scope to effect; '
            'got a string. Write `permissions: [read, write]`.'
        )

    if isinstance(raw, (list, tuple)):
        rules: list[Rule] = []
        scopes = _scopes(raw, 'permissions')
        if scopes:
            # A bare list is an allowlist, which only means something against a
            # closed baseline: everything off, then these on.
            rules.append(Rule('*', Effect.DENY))
            rules.extend(Rule(scope, Effect.ALLOW) for scope in scopes)
        else:
            rules.append(Rule('*', Effect.DENY))
        return ToolPermissions(rules=tuple(rules))

    if isinstance(raw, dict):
        rules = []
        for scope, value in raw.items():
            rules.extend(_rules_for(str(scope), value))
        return ToolPermissions(rules=tuple(rules))

    raise AgentParseError(
        f'`permissions` must be a list of scopes or a mapping of scope to effect, got {type(raw).__name__}.'
    )


def _rules_for(scope: str, value: Any) -> list[Rule]:
    """Expands one ``scope: value`` entry of the mapping form."""
    name = scope.strip()
    if not name:
        raise AgentParseError('`permissions` cannot have an empty scope.')
    if name in {'allow', 'deny'}:
        raise AgentParseError(_LEGACY_HINT)
    _require_known_scope(name)
    canonical = _canonical_scope(name)

    if isinstance(value, str):
        return [Rule(canonical, _effect(value, name))]
    if isinstance(value, bool):
        raise AgentParseError(f'`permissions.{name}` must be `allow`, `deny` or a mapping with both.')
    if isinstance(value, (list, tuple)):
        raise AgentParseError(
            f'`permissions.{name}` needs an effect: write `{name}: allow`, `{name}: deny`, '
            f'or `{name}: {{allow: ["<pattern>"]}}` for specific resources.'
        )
    if isinstance(value, dict):
        return _effect_rules(canonical, value)

    raise AgentParseError(
        f'`permissions.{name}` must be `allow`, `deny` or a mapping with `allow` and `deny`, '
        f'got {type(value).__name__}.'
    )


def _effect_rules(scope: str, value: dict[Any, Any]) -> list[Rule]:
    """Expands ``{allow: [...], deny: [...]}``: allow first, so a denial wins."""
    unknown = {str(key) for key in value} - set(_EFFECTS)
    if unknown:
        names = ', '.join(sorted(unknown))
        raise AgentParseError(f'Unknown keys in `permissions.{scope}`: {names}. Use `allow` and `deny`.')
    if not value:
        raise AgentParseError(f'`permissions.{scope}` needs `allow`, `deny` or both.')

    rules: list[Rule] = []
    for effect_name in (Effect.ALLOW, Effect.DENY):
        if effect_name not in value:
            continue
        patterns = _patterns(value[effect_name], f'permissions.{scope}.{effect_name}')
        rules.append(Rule(scope, effect_name, patterns))
    return rules


def _effect(value: str, scope: str) -> Effect:
    effect = _EFFECTS.get(value.strip().lower())
    if effect is None:
        raise AgentParseError(f'`permissions.{scope}` must be `allow` or `deny`, got {value!r}.')
    return effect


def _require_known_scope(scope: str) -> None:
    if not scope_is_known(scope):
        raise AgentParseError(scope_problem(scope))


def _scopes(raw: list[Any] | tuple[Any, ...], field_name: str) -> tuple[str, ...]:
    scopes: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise AgentParseError(f'`{field_name}` entries must be strings.')
        scope = _canonical_scope(item.strip())
        if not scope:
            continue
        if scope in {'allow', 'deny'}:
            raise AgentParseError(_LEGACY_HINT)
        _require_known_scope(scope)
        scopes.append(scope)
    return tuple(scopes)


def _patterns(value: Any, field_name: str) -> tuple[str, ...]:
    """The patterns of one effect.

    A string is **one** pattern and not a comma-separated list, because a
    command can contain a comma: ``git log --format=%s%d,%H`` is a single rule.
    A list is a list.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, (list, tuple, set, frozenset)):
        items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise AgentParseError(f'`{field_name}` entries must be strings.')
            text = item.strip()
            if text:
                items.append(text)
        return tuple(items)
    raise AgentParseError(f'`{field_name}` must be a string or a list of strings.')


__all__ = ['UNRESTRICTED', 'Effect', 'Rule', 'ToolPermissions', 'parse_permissions']
