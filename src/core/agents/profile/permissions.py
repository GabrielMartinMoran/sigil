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

from typing import Any

from src.core.agents.profile.effect import Effect
from src.core.agents.profile.models.agent_parse_error import AgentParseError
from src.core.agents.profile.rule import Rule
from src.core.agents.profile.tool_permissions import ToolPermissions
from src.core.tooling.permission import ALL_SCOPES, scope_is_known, scope_problem

UNRESTRICTED = ToolPermissions()
"""What a profile that says nothing gets."""


# --- matching ----------------------------------------------------------------


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
