"""Capability that applies the permissions of the active agent profile.

Two hooks, because a profile rules on two different things:

- ``prepare_tools`` drops the tools a whole-tool ``deny`` covers. A filtered
  tool is not "refused at call time": it never appears in the tool definitions of
  the request, so the model does not know it exists and cannot plan around it.
- ``before_tool_execute`` checks the call itself, for the tools a rule narrowed
  by resource instead of closing. The tool stays visible — the model needs it —
  and the specific path, command or URL is refused. The refusal goes back as a
  failed result, so the model adapts inside the same turn instead of the turn
  dying on an exception.

It reads the profile through a ``ProfileSource``, per request and never at
construction. For the agent at the root of the session that source is the
registry, so switching agent with `/agent` repaints the tool set on the next
request without rebuilding the agent. For a subagent it is the profile that
summoned it, which does not change for the length of its run.
"""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed

from src.engine.tooling.permission import ResourceKind, permission_of, resource_values

if TYPE_CHECKING:
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import RunContext, ToolDefinition

    from src.engine.agents.profile.models.agent_profile import AgentProfile
    from src.engine.agents.profile.source import ProfileSource

DENIED_PREFIX = 'PermissionDenied'
"""Prefix of the message, so a refusal is greppable in the history."""


@dataclass
class ToolPermissionsCapability(AbstractCapability[Any]):
    """Filters the tool definitions and refuses the calls a profile forbids."""

    _: KW_ONLY

    source: ProfileSource
    id: str | None = 'agent_permissions'
    """One per agent: it is the profile in effect, and there is only one."""

    def _profile(self) -> AgentProfile | None:
        return self.source.profile()

    async def prepare_tools(
        self,
        ctx: RunContext[Any],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        del ctx
        profile = self._profile()
        if profile is None or not profile.permissions.restricted:
            return tool_defs
        return [tool_def for tool_def in tool_defs if self._advertises(profile, tool_def)]

    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        del ctx
        profile = self._profile()
        if profile is None or not profile.permissions.restricted:
            return args
        if not self._advertises(profile, tool_def):
            # Belt and braces: `prepare_tools` already hid it, so reaching here
            # means a call that survived from another step.
            raise ToolFailed(_message(tool_def.name, ()))
        declaration = permission_of(tool_def)
        groups = () if declaration is None else declaration.groups
        resources = resource_values(declaration, args)
        kind = ResourceKind.TEXT if declaration is None else declaration.kind
        if not profile.permissions.permits(tool_def.name, groups, resources, kind):
            raise ToolFailed(_message(tool_def.name, resources))
        return args

    def _advertises(self, profile: AgentProfile, tool_def: ToolDefinition) -> bool:
        declaration = permission_of(tool_def)
        groups = () if declaration is None else declaration.groups
        return profile.permissions.advertises(tool_def.name, groups)


def _message(tool_name: str, resources: tuple[str, ...]) -> str:
    """What the model reads when a call is refused."""
    detail = ''
    if resources:
        detail = f' on {", ".join(repr(resource) for resource in resources)}'
    return (
        f'{DENIED_PREFIX}: the active agent profile does not allow `{tool_name}`{detail}. '
        'Do not retry the same call: pick another approach, or tell the user that the '
        'active agent cannot do it.'
    )


__all__ = ['DENIED_PREFIX', 'ToolPermissionsCapability']
