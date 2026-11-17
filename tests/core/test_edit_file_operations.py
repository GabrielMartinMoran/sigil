"""Behavior of batched file edits."""

import asyncio
from pathlib import Path

import pytest

from src.core.files.models.file_tool_error import FileToolError
from src.core.files.tools import register_file_tools


class Agent:
    def __init__(self) -> None:
        self.tools = {}

    def tool_plain(self, func, **kwargs):
        self.tools[kwargs.get('name') or func.__name__] = func
        return func

    tool = tool_plain


def edit_tool():
    agent = Agent()
    register_file_tools(agent)
    assert 'edit_file' not in agent.tools
    assert 'patch_file' not in agent.tools

    def call(*args, **kwargs):
        return asyncio.run(agent.tools['write_file'](*args, **kwargs))

    return call


def test_edit_applies_operations_in_order(tmp_path: Path) -> None:
    path = tmp_path / 'file.txt'
    path.write_bytes(b'one\r\ntwo\r\n')
    edit_tool()(
        path=str(path),
        operations=[
            {'type': 'replace', 'old': 'one', 'new': 'ONE'},
            {'type': 'replace_lines', 'start_line': 2, 'end_line': 2, 'new': 'TWO', 'expected_text': 'two'},
            {'type': 'append', 'content': 'end'},
        ],
    )
    assert path.read_bytes() == b'ONE\r\nTWO\r\nend'


def test_failed_operation_leaves_file_untouched(tmp_path: Path) -> None:
    path = tmp_path / 'file.txt'
    path.write_text('one')
    with pytest.raises(FileToolError, match='2'):
        edit_tool()(
            path=str(path),
            operations=[
                {'type': 'replace', 'old': 'one', 'new': 'two'},
                {'type': 'replace', 'old': 'missing', 'new': ''},
            ],
        )
    assert path.read_text() == 'one'


def test_edit_rejects_hardlinked_file_without_changing_either_link(tmp_path: Path) -> None:
    path = tmp_path / 'file.txt'
    alias = tmp_path / 'alias.txt'
    path.write_text('original')
    alias.hardlink_to(path)
    with pytest.raises(FileToolError, match='hard link'):
        edit_tool()(path=str(path), operations=[{'type': 'write', 'content': 'changed'}])
    assert path.read_text() == alias.read_text() == 'original'


def test_new_file_uses_normal_creation_permissions(tmp_path: Path) -> None:
    reference = tmp_path / 'reference.txt'
    reference.write_text('reference')
    path = tmp_path / 'file.txt'
    edit_tool()(path=str(path), operations=[{'type': 'write', 'content': 'hello'}])
    assert path.stat().st_mode & 0o777 == reference.stat().st_mode & 0o777


def test_write_creates_parents_and_replace_can_delete(tmp_path: Path) -> None:
    path = tmp_path / 'new' / 'file.txt'
    edit_tool()(
        path=str(path),
        operations=[
            {'type': 'write', 'content': 'hello world'},
            {'type': 'replace', 'old': ' world', 'new': ''},
        ],
    )
    assert path.read_text() == 'hello'


def test_write_replaces_existing_non_utf8_file(tmp_path: Path) -> None:
    path = tmp_path / 'binary.dat'
    path.write_bytes(b'\xff\xfe')
    edit_tool()(path=str(path), operations=[{'type': 'write', 'content': 'text'}])
    assert path.read_bytes() == b'text'


def test_edit_preserves_extended_attributes(tmp_path: Path) -> None:
    import os

    if not hasattr(os, 'setxattr'):
        pytest.skip('extended attributes unavailable')
    path = tmp_path / 'file.txt'
    path.write_text('original')
    try:
        os.setxattr(path, 'user.sigil_test', b'protected metadata')
    except OSError as exc:
        pytest.skip(f'extended attributes unavailable: {exc}')
    edit_tool()(path=str(path), operations=[{'type': 'write', 'content': 'updated'}])
    assert os.getxattr(path, 'user.sigil_test') == b'protected metadata'


def test_edit_leaves_file_unchanged_if_metadata_cannot_be_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    if not hasattr(os, 'setxattr'):
        pytest.skip('extended attributes unavailable')
    path = tmp_path / 'file.txt'
    path.write_text('original')
    try:
        os.setxattr(path, 'user.sigil_test', b'metadata')
    except OSError as exc:
        pytest.skip(f'extended attributes unavailable: {exc}')

    def fail(*args, **kwargs):
        raise PermissionError('cannot preserve attribute')

    monkeypatch.setattr(os, 'setxattr', fail)
    with pytest.raises((FileToolError, OSError)):
        edit_tool()(path=str(path), operations=[{'type': 'write', 'content': 'updated'}])
    assert path.read_text() == 'original'
    assert os.getxattr(path, 'user.sigil_test') == b'metadata'


def test_edit_preserves_posix_acl(tmp_path: Path) -> None:
    import os
    import shutil
    import subprocess

    setfacl = shutil.which('setfacl')
    if setfacl is None or not hasattr(os, 'getxattr'):
        pytest.skip('POSIX ACL tools unavailable')
    path = tmp_path / 'file.txt'
    path.write_text('original')
    result = subprocess.run([setfacl, '-m', 'u:12345:r--', str(path)], capture_output=True, check=False)
    if result.returncode:
        pytest.skip('filesystem does not support POSIX ACLs')
    acl = os.getxattr(path, 'system.posix_acl_access')
    edit_tool()(path=str(path), operations=[{'type': 'write', 'content': 'updated'}])
    assert os.getxattr(path, 'system.posix_acl_access') == acl


def test_independent_writers_can_lose_updates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from src.core.files import tools

    path = tmp_path / 'shared.txt'
    path.write_text('initial')
    barrier = Barrier(2, timeout=5)
    read = tools._read_text_for_patch

    def synchronized_read(target, name):
        value = read(target, name)
        barrier.wait()
        return value

    monkeypatch.setattr(tools, '_read_text_for_patch', synchronized_read)
    write = edit_tool()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(write, path=str(path), operations=[{'type': 'append', 'content': suffix}])
            for suffix in ('-one', '-two')
        ]
        for future in futures:
            future.result(timeout=10)
    assert path.read_text() in ('initial-one', 'initial-two')


def test_write_through_symlink_keeps_link_and_updates_target(tmp_path: Path) -> None:
    target = tmp_path / 'target.txt'
    target.write_text('original')
    link = tmp_path / 'link.txt'
    link.symlink_to(target)
    edit_tool()(path=str(link), operations=[{'type': 'write', 'content': 'updated'}])
    assert link.is_symlink()
    assert target.read_text() == 'updated'


def test_write_through_dangling_symlink_creates_target(tmp_path: Path) -> None:
    target = tmp_path / 'target.txt'
    link = tmp_path / 'link.txt'
    link.symlink_to(target)
    edit_tool()(path=str(link), operations=[{'type': 'write', 'content': 'created'}])
    assert link.is_symlink()
    assert target.read_text() == 'created'


def test_edit_fails_closed_when_xattr_api_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    path = tmp_path / 'file.txt'
    path.write_text('original')
    monkeypatch.delattr(os, 'listxattr', raising=False)
    with pytest.raises(FileToolError, match='extended attributes'):
        edit_tool()(path=str(path), operations=[{'type': 'write', 'content': 'updated'}])
    assert path.read_text() == 'original'
    assert sorted(item.name for item in tmp_path.iterdir()) == ['file.txt']
