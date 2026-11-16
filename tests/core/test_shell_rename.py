"""The shell tool has a descriptive public name and keeps run-group policy."""

from src.core.agents.profile.permissions import parse_permissions
from src.core.shell.tools import register_shell_tools
from src.core.tooling.resource_kind import ResourceKind
from src.core.tooling.tool_group import ToolGroup


class Agent:
    def __init__(self) -> None:
        self.tools = {}

    def tool_plain(self, func, **kwargs):
        self.tools[kwargs.get('name') or func.__name__] = func
        return func

    tool = tool_plain


def test_shell_replaces_run() -> None:
    agent = Agent()
    register_shell_tools(agent)
    assert 'shell' in agent.tools
    assert 'run' not in agent.tools


def test_legacy_wildcard_denial_restricts_shell() -> None:
    permissions = parse_permissions({'r*': 'deny'})
    assert not permissions.permits('shell', (ToolGroup.RUN,), ('ls',), ResourceKind.COMMAND)


def test_legacy_run_group_restricts_shell() -> None:
    permissions = parse_permissions({'run': {'allow': ['git status *']}})
    assert permissions.permits('shell', (ToolGroup.RUN,), ('git status',), ResourceKind.COMMAND)
    assert not permissions.permits('shell', (ToolGroup.RUN,), ('rm -rf /',), ResourceKind.COMMAND)
