"""What a tool is, so a profile can rule on more than the tool's name.

A permission rule is written against a **scope**: ``*``, a **group** (``read``,
``write``, ``run``…) or a **tool name** (``patch_file``, ``mcp_github_*``). The
group vocabulary lives here, next to the tools that fill it, and not in the
profile parser: the parser must not need to know that ``patch_file`` writes.

Some tools also act on a **resource**: the path they read, the commands they run,
the URL they fetch. Declaring it is what lets a rule say "the shell, but only
``git status``" instead of just "the shell". The value arrives as an argument on
every call, and the policy matches the rule's patterns against it.

The declaration travels as tool metadata, not as a wrapper: ``prepare_tools``
reads it off the tool definition to decide what to advertise, and
``before_tool_execute`` reads it again to decide on the concrete call. Nothing in
a tool's signature changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

PERMISSION_METADATA_KEY = 'sigil_permission'
"""Key of the declaration inside ``ToolDefinition.metadata``."""


class ToolGroup(StrEnum):
    """The coarse vocabulary a profile can rule on.

    One group per tool, chosen by what the tool *does* and not by the module it
    lives in: an agent that must not touch the disk denies ``read`` and ``write``
    and keeps the rest.
    """

    RUN = 'run'
    """Executes a process: the shell, and the scripts bundled inside a skill."""

    READ = 'read'
    """Reads a file. The path is the resource."""

    WRITE = 'write'
    """Creates or edits a file. The path is the resource."""

    WEB = 'web'
    """Reaches the network. The search query or the URL is the resource."""

    SKILLS = 'skills'
    """Lists, loads and reads Agent Skills and their bundled resources."""

    MCP = 'mcp'
    """The meta-tools over MCP servers, resources and prompts."""

    ASK = 'ask'
    """Talks to the user mid-turn."""

    ROADMAP = 'roadmap'
    """Publishes the task roadmap the UI shows."""

    SUMMON = 'summon'
    """Starts, resumes, steers and stops subagents: the tree, as one capability."""


BUILTIN_TOOLS: Mapping[str, ToolGroup] = {
    'run': ToolGroup.RUN,
    'read_file': ToolGroup.READ,
    'write_file': ToolGroup.WRITE,
    'patch_file': ToolGroup.WRITE,
    'web_search': ToolGroup.WEB,
    'read_url': ToolGroup.WEB,
    'list_skills': ToolGroup.SKILLS,
    'load_skill': ToolGroup.SKILLS,
    'read_skill_resource': ToolGroup.SKILLS,
    'run_skill_script': ToolGroup.SKILLS,
    'list_mcp_servers': ToolGroup.MCP,
    'list_mcp_resources': ToolGroup.MCP,
    'read_mcp_resource': ToolGroup.MCP,
    'list_mcp_prompts': ToolGroup.MCP,
    'get_mcp_prompt': ToolGroup.MCP,
    'ask_user': ToolGroup.ASK,
    'update_roadmap': ToolGroup.ROADMAP,
    'summon': ToolGroup.SUMMON,
    'send_message': ToolGroup.SUMMON,
    'ack_notifications': ToolGroup.SUMMON,
    'kill_subagent': ToolGroup.SUMMON,
    'list_agents': ToolGroup.SUMMON,
}
"""Every built-in tool and the group it belongs to.

This table is the vocabulary a profile is validated against, so it is also the
one that has to stay honest: ``tests/test_agent_permissions.py`` reads the
declarations off the real registrars and fails if one of them drifts from here.
"""

GROUP_NAMES: tuple[str, ...] = tuple(str(group) for group in ToolGroup)
"""The group names, in declaration order."""

BUILTIN_TOOL_NAMES: frozenset[str] = frozenset(BUILTIN_TOOLS)
"""Names a profile can target exactly."""

WILDCARD_CHARACTERS = ('*', '?', '[')
"""Characters that turn a scope into a pattern instead of a literal name."""

ALL_SCOPES = frozenset({'*', 'all'})
"""Spellings of "every tool".

``all`` is not an alias for convenience: a bare ``*`` is a YAML alias, so
``*: deny`` does not even parse and the rule has to be quoted as ``'*': deny``.
The unquoted spelling is the one the docs use, and both reach the same rule.
"""


class ResourceKind(StrEnum):
    """How the resource value is normalized before it meets a pattern."""

    TEXT = 'text'
    """Matched as given: a search query, a skill name, a server name."""

    PATH = 'path'
    """Matched as a path, both workspace-relative and absolute."""

    COMMAND = 'command'
    """Matched as a shell command, with its whitespace collapsed."""


@dataclass(frozen=True, slots=True)
class ToolPermission:
    """What a tool is, for the permission filter of the active profile."""

    groups: tuple[str, ...]
    """Coarse categories the tool belongs to; matched as exact names."""

    resource: str | None = None
    """Argument whose value the resource patterns are matched against."""

    kind: ResourceKind = ResourceKind.TEXT
    """How that value is normalized before matching."""

    def metadata(self) -> dict[str, Any]:
        """The declaration as tool metadata."""
        return {
            PERMISSION_METADATA_KEY: {
                'groups': [str(group) for group in self.groups],
                'resource': self.resource,
                'kind': str(self.kind),
            }
        }


def has_wildcard(scope: str) -> bool:
    """Whether ``scope`` is a pattern rather than a literal name."""
    return any(character in scope for character in WILDCARD_CHARACTERS)


def permission_of(tool_def: Any) -> ToolPermission | None:
    """Reads the declaration off a tool definition, or ``None`` if it has none.

    A tool without one — every tool that comes from an MCP server, or a plain
    ``@agent.tool_plain`` in a test — is matched by its name alone.
    """
    metadata = getattr(tool_def, 'metadata', None)
    if not isinstance(metadata, Mapping):
        return None
    raw = metadata.get(PERMISSION_METADATA_KEY)
    if not isinstance(raw, Mapping):
        return None
    return ToolPermission(
        groups=tuple(str(group) for group in raw.get('groups') or ()),
        resource=_optional_string(raw.get('resource')),
        kind=_kind(raw.get('kind')),
    )


def resource_values(declaration: ToolPermission | None, args: Mapping[str, Any]) -> tuple[str, ...]:
    """The values a call acts on; empty when there is nothing to match.

    ``run`` receives several commands, so one call can carry several resources:
    the policy refuses the call when any of them is denied.
    """
    if declaration is None or declaration.resource is None:
        return ()
    value = args.get(declaration.resource)
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(text for text in (str(item).strip() for item in value if item is not None) if text)
    text = str(value).strip()
    return (text,) if text else ()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def scope_is_known(scope: str) -> bool:
    """Whether a profile may use ``scope`` as a permission key.

    Wildcards are always accepted: that is how a profile targets an MCP server,
    whose tools are not known until it connects.
    """
    return scope in ALL_SCOPES or scope in GROUP_NAMES or scope in BUILTIN_TOOL_NAMES or has_wildcard(scope)


def scope_problem(scope: str) -> str:
    """The message a profile gets for a key nobody can match.

    A typo in a permission is the one mistake that must not pass silently: it
    would leave the tool in place while the profile reads as if it were closed.
    """
    known = ', '.join(dict.fromkeys([*sorted(ALL_SCOPES), *GROUP_NAMES, *sorted(BUILTIN_TOOL_NAMES)]))
    return (
        f'Unknown permission scope {scope!r}. Use `all`, a group or a built-in tool ({known}). '
        'A tool that Sigil does not ship — every MCP tool — is targeted with a wildcard over '
        'its names, e.g. `mcp_github_*`.'
    )


def _kind(value: Any) -> ResourceKind:
    if value is None:
        return ResourceKind.TEXT
    try:
        return ResourceKind(value)
    except ValueError:
        # An unknown kind is a bug in the tool, not a reason to refuse the call:
        # it falls back to comparing the value as text.
        return ResourceKind.TEXT


__all__ = [
    'BUILTIN_TOOLS',
    'BUILTIN_TOOL_NAMES',
    'GROUP_NAMES',
    'PERMISSION_METADATA_KEY',
    'ResourceKind',
    'ToolGroup',
    'ToolPermission',
    'has_wildcard',
    'permission_of',
    'resource_values',
    'scope_is_known',
    'scope_problem',
]
