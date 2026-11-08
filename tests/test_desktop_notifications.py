"""Desktop delivery is bounded, optional and never includes conversation content."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.ui.desktop_notifications import Attention, notification_command, send_notification


@pytest.mark.parametrize('event', list(Attention))
def test_should_build_linux_notification_without_shell(event: Attention) -> None:
    command = notification_command(event, platform='linux', which=lambda name: f'/usr/bin/{name}')
    assert command is not None
    assert command[0] == '/usr/bin/notify-send'
    assert '--app-name=Sigil' in command
    assert event.value in command


def test_should_build_macos_notification_with_fixed_applescript() -> None:
    command = notification_command(Attention.QUESTION, platform='darwin', which=lambda name: f'/usr/bin/{name}')
    assert command is not None
    assert command[0] == '/usr/bin/osascript'
    assert 'display notification' in command[2]
    assert Attention.QUESTION.value in command


@pytest.mark.parametrize('platform', ['linux', 'darwin', 'win32', 'unsupported'])
def test_should_skip_when_backend_is_unavailable(platform: str) -> None:
    assert notification_command(Attention.FINISHED, platform=platform, which=lambda *args: None) is None


def test_should_bound_delivery_and_discard_command_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        'src.ui.desktop_notifications.notification_command', lambda *args: ['notify-send', 'Sigil', 'Ready']
    )
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr('src.ui.desktop_notifications.subprocess.run', run)
    assert send_notification(Attention.FINISHED)
    assert run.call_args.kwargs['timeout'] == 3
    assert run.call_args.kwargs['stdout'] == subprocess.DEVNULL
    assert not run.call_args.kwargs.get('shell', False)


@pytest.mark.parametrize('failure', [OSError('missing'), subprocess.TimeoutExpired('notify-send', 3)])
def test_should_not_raise_when_desktop_delivery_fails(monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    monkeypatch.setattr('src.ui.desktop_notifications.notification_command', lambda *args: ['notify-send'])
    monkeypatch.setattr('src.ui.desktop_notifications.subprocess.run', Mock(side_effect=failure))
    assert not send_notification(Attention.FINISHED)


def test_should_report_rejected_delivery_and_missing_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('src.ui.desktop_notifications.notification_command', lambda *args: None)
    assert not send_notification(Attention.FINISHED)
    monkeypatch.setattr('src.ui.desktop_notifications.notification_command', lambda *args: ['notify-send'])
    monkeypatch.setattr(
        'src.ui.desktop_notifications.subprocess.run', Mock(return_value=subprocess.CompletedProcess([], 1))
    )
    assert not send_notification(Attention.FINISHED)


@pytest.mark.parametrize('returncode', [0, 7])
def test_should_deliver_literal_arguments_through_real_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    import sys

    output = tmp_path / 'delivered.txt'
    script = 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2]); sys.exit(int(sys.argv[3]))'
    monkeypatch.setattr(
        'src.ui.desktop_notifications.notification_command',
        lambda event, context: [sys.executable, '-c', script, str(output), event.value, str(returncode)],
    )
    assert send_notification(Attention.FINISHED) is (returncode == 0)
    assert output.read_text() == Attention.FINISHED.value


def test_should_include_project_and_title_without_unsafe_applescript_interpolation() -> None:
    from src.ui.desktop_notifications import NotificationContext

    context = NotificationContext('repo:worktree', 'Title "quoted"\nnext \\ end')
    linux = notification_command(Attention.QUESTION, context, platform='linux', which=lambda name: name)
    mac = notification_command(Attention.QUESTION, context, platform='darwin', which=lambda name: name)
    assert linux is not None and 'repo:worktree — Title "quoted" next \\ end' in linux
    assert mac is not None
    assert 'Title "quoted"' not in mac[2]
    assert 'repo:worktree — Title "quoted" next \\ end' in mac
    assert Attention.QUESTION.value in mac


def test_should_fall_back_without_exposing_message_content() -> None:
    from src.ui.desktop_notifications import NotificationContext

    command = notification_command(
        Attention.FINISHED, NotificationContext('', ''), platform='linux', which=lambda name: name
    )
    assert command is not None
    assert 'Sigil' in command
    assert Attention.FINISHED.value in command
