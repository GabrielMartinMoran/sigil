"""Compatibility of renamed file tools with existing profiles."""

from src.core.agents.profile.permissions import parse_permissions
from src.core.tooling.resource_kind import ResourceKind
from src.core.tooling.tool_group import ToolGroup


def test_legacy_denial_does_not_allow_batched_edit() -> None:
    permissions = parse_permissions({'write_file': 'deny'})
    assert not permissions.permits('write_file', (ToolGroup.WRITE,), ('file.txt',), ResourceKind.PATH)


def test_edit_permission_without_legacy_names_works() -> None:
    permissions = parse_permissions({'*': 'deny', 'write_file': 'allow'})
    assert permissions.permits('write_file', (ToolGroup.WRITE,), ('file.txt',), ResourceKind.PATH)


def test_legacy_file_allows_preserve_access_with_write_group_denied() -> None:
    permissions = parse_permissions(
        {
            'write': 'deny',
            'write_file': {'allow': ['allowed/**']},
            'patch_file': {'allow': ['allowed/**']},
        }
    )
    assert permissions.advertises('write_file', (ToolGroup.WRITE,))
    assert permissions.permits('write_file', (ToolGroup.WRITE,), ('allowed/file.txt',), ResourceKind.PATH)
    assert not permissions.permits('write_file', (ToolGroup.WRITE,), ('other/file.txt',), ResourceKind.PATH)
