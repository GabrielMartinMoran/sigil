"""The ToolPermissions value of the agents layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.agents.profile.effect import Effect
from src.core.agents.profile.rule import Rule
from src.core.tooling.resource_kind import ResourceKind


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
        *,
        root: Path | None = None,
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
                if not rule.matches_resource(resources, kind, root):
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


def _unique(values: Any) -> list[str]:
    """The scopes in the order they are written, once each."""
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(str(value), None)
    return list(seen)
