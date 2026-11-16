"""Legacy patch restrictions remain effective for the unified write tool."""

from src.core.agents.profile.permissions import parse_permissions
from src.core.tooling.resource_kind import ResourceKind
from src.core.tooling.tool_group import ToolGroup


def test_patch_denial_cannot_be_bypassed_by_write() -> None:
    permissions = parse_permissions({'write': 'allow', 'patch_file': 'deny'})
    assert not permissions.advertises('write_file', (ToolGroup.WRITE,))
    assert not permissions.permits('write_file', (ToolGroup.WRITE,), ('file.txt',), ResourceKind.PATH)


def test_old_allowlists_intersect() -> None:
    permissions = parse_permissions(
        {
            'write': 'deny',
            'write_file': {'allow': ['allowed/**']},
            'patch_file': {'allow': ['allowed/**']},
        }
    )
    assert permissions.advertises('write_file', (ToolGroup.WRITE,))
    assert permissions.permits('write_file', (ToolGroup.WRITE,), ('allowed/x',), ResourceKind.PATH)
    assert not permissions.permits('write_file', (ToolGroup.WRITE,), ('other/x',), ResourceKind.PATH)
