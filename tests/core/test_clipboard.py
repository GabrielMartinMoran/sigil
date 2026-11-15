"""Reading an image from the clipboard, per desktop.

A terminal hands a program text and nothing else: a screenshot on the clipboard
arrives as an empty paste. These tests declare a machine — which tools exist, which
session it is — and check which command is asked for the bytes. No desktop, no
subprocess, no image: the runners are fakes.
"""

from __future__ import annotations

import pytest

from src.engine.attachments.clipboard import (
    MACOS_TOOL,
    PNG_TYPE,
    ClipboardUnavailable,
    CommandResult,
    read_clipboard_image,
)

PNG_BYTES = b'\x89PNG\r\n\x1a\nfake'


class FakeDesktop:
    """Records the commands the code asks for and answers each one."""

    def __init__(self, answers: dict[tuple[str, ...], CommandResult]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        return self.answers.get(tuple(args), CommandResult(returncode=1, stdout=b''))

    @property
    def called(self) -> list[tuple[str, ...]]:
        return [tuple(call) for call in self.calls]


def _which(*installed: str):
    def finder(name: str) -> str | None:
        return f'/usr/bin/{name}' if name in installed else None

    return finder


X11_TARGETS = CommandResult(
    returncode=0,
    stdout=b'TARGETS\nUTF8_STRING\nimage/png\nimage/bmp\nTEXT\n',
)

X11_PNG = CommandResult(returncode=0, stdout=PNG_BYTES)


# --- X11 --------------------------------------------------------------------


def test_x11_asks_for_the_targets_and_then_for_png():
    """The targets decide; asking for a type nobody offered is how you get garbage."""
    desktop = FakeDesktop(
        {
            ('xclip', '-selection', 'clipboard', '-t', 'TARGETS', '-o'): X11_TARGETS,
            ('xclip', '-selection', 'clipboard', '-t', 'image/png', '-o'): X11_PNG,
        }
    )

    image = read_clipboard_image(
        environ={'XDG_SESSION_TYPE': 'x11'},
        platform='linux',
        run=desktop,
        which=_which('xclip'),
    )

    assert image == PNG_BYTES
    assert desktop.called[-1] == ('xclip', '-selection', 'clipboard', '-t', 'image/png', '-o')


def test_x11_prefers_png_over_a_type_that_needs_transcoding():
    desktop = FakeDesktop(
        {
            ('xclip', '-selection', 'clipboard', '-t', 'TARGETS', '-o'): CommandResult(
                returncode=0, stdout=b'image/bmp\nimage/png\n'
            ),
            ('xclip', '-selection', 'clipboard', '-t', 'image/png', '-o'): X11_PNG,
        }
    )

    assert read_clipboard_image(
        environ={'XDG_SESSION_TYPE': 'x11'}, platform='linux', run=desktop, which=_which('xclip')
    )
    assert ('xclip', '-selection', 'clipboard', '-t', 'image/png', '-o') in desktop.called
    assert not any('image/bmp' in call for call in desktop.called)


def test_a_text_only_clipboard_yields_no_image_instead_of_noise():
    """Copying a paragraph must not produce a broken attachment."""
    desktop = FakeDesktop(
        {
            ('xclip', '-selection', 'clipboard', '-t', 'TARGETS', '-o'): CommandResult(
                returncode=0, stdout=b'TARGETS\nUTF8_STRING\nTEXT\n'
            ),
        }
    )

    assert (
        read_clipboard_image(environ={'XDG_SESSION_TYPE': 'x11'}, platform='linux', run=desktop, which=_which('xclip'))
        is None
    )
    # One call: it stopped at the targets instead of asking for a type nobody offered.
    assert len(desktop.calls) == 1


def test_x11_without_xclip_explains_what_to_install():
    with pytest.raises(ClipboardUnavailable) as caught:
        read_clipboard_image(environ={'XDG_SESSION_TYPE': 'x11'}, platform='linux', run=FakeDesktop({}), which=_which())

    assert 'xclip' in str(caught.value)


# --- Wayland ----------------------------------------------------------------


def test_wayland_uses_wl_paste_and_never_xclip():
    """A Wayland session with XWayland answers `xclip` from a stale X selection."""
    desktop = FakeDesktop(
        {
            ('wl-paste', '--list-types'): CommandResult(returncode=0, stdout=b'image/png\ntext/plain\n'),
            ('wl-paste', '--type', PNG_TYPE): CommandResult(returncode=0, stdout=PNG_BYTES),
        }
    )

    image = read_clipboard_image(
        environ={'XDG_SESSION_TYPE': 'wayland', 'DISPLAY': ':0'},
        platform='linux',
        run=desktop,
        which=_which('wl-paste', 'xclip'),
    )

    assert image == PNG_BYTES
    assert all(call[0] == 'wl-paste' for call in desktop.called)


def test_wayland_is_detected_from_the_socket_when_the_session_is_not_declared():
    desktop = FakeDesktop(
        {
            ('wl-paste', '--list-types'): CommandResult(returncode=0, stdout=b'image/png\n'),
            ('wl-paste', '--type', PNG_TYPE): CommandResult(returncode=0, stdout=PNG_BYTES),
        }
    )

    assert (
        read_clipboard_image(
            environ={'WAYLAND_DISPLAY': 'wayland-0'},
            platform='linux',
            run=desktop,
            which=_which('wl-paste', 'xclip'),
        )
        == PNG_BYTES
    )


def test_wayland_without_wl_clipboard_explains_what_to_install():
    with pytest.raises(ClipboardUnavailable) as caught:
        read_clipboard_image(
            environ={'XDG_SESSION_TYPE': 'wayland'},
            platform='linux',
            run=FakeDesktop({}),
            which=_which('xclip'),
        )

    assert 'wl-clipboard' in str(caught.value)


# --- the other desktops -----------------------------------------------------


def test_macos_reads_pngpaste_and_names_it_when_missing():
    desktop = FakeDesktop({(MACOS_TOOL, '-'): CommandResult(returncode=0, stdout=PNG_BYTES)})

    assert read_clipboard_image(platform='darwin', run=desktop, which=_which(MACOS_TOOL)) == PNG_BYTES

    with pytest.raises(ClipboardUnavailable) as caught:
        read_clipboard_image(platform='darwin', run=FakeDesktop({}), which=_which())
    assert 'brew install pngpaste' in str(caught.value)


def test_windows_saves_to_a_temp_file_and_reads_it_back(tmp_path):
    """PowerShell has no way to write raw bytes to stdout, so it writes a file."""
    target = tmp_path / 'shot.png'
    target.write_bytes(PNG_BYTES)
    desktop = FakeDesktop(
        {
            (
                'powershell',
                '-NoProfile',
                '-Command',
                __import__('src.engine.attachments.clipboard', fromlist=['WINDOWS_SCRIPT']).WINDOWS_SCRIPT,
            ): CommandResult(returncode=0, stdout=str(target).encode()),
        }
    )

    assert read_clipboard_image(platform='win32', run=desktop) == PNG_BYTES
    # The temporary file is not left behind.
    assert not target.exists()


def test_a_failing_tool_is_no_image_rather_than_an_error():
    desktop = FakeDesktop(
        {('xclip', '-selection', 'clipboard', '-t', 'TARGETS', '-o'): CommandResult(returncode=1, stdout=b'')}
    )

    assert (
        read_clipboard_image(environ={'XDG_SESSION_TYPE': 'x11'}, platform='linux', run=desktop, which=_which('xclip'))
        is None
    )
