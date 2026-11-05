"""Reading an image from the system clipboard.

A terminal hands a program text and nothing else: bracketed paste carries
characters, so a screenshot on the clipboard arrives as an empty paste. The bytes
have to be fetched from the desktop itself, and each desktop has its own tool.
That is the whole reason this module exists, and why it is a set of strategies
with a fallback instead of one command.

The order matters. The session type decides first, because a Wayland session with
XWayland installed answers `xclip` from a stale X selection and hands back the
wrong image — or no image, with `wl-paste` sitting right there. When nothing can
be read, `None` is returned and the composer falls back to the text paste it
already had: no image tool installed must never mean "paste stopped working".
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

TIMEOUT_SECONDS = 3.0
"""A clipboard read that takes longer than this is a hung tool, not a big image."""

IMAGE_TYPE_PREFIX = 'image/'


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What a clipboard tool answered. `returncode != 0` is a normal outcome here."""

    returncode: int
    stdout: bytes
    stderr: str = ''

    @property
    def ok(self) -> bool:
        return self.returncode == 0


RunCommand = Callable[[Sequence[str], float], CommandResult]
"""Runs a command and captures its bytes. Injected so the tests never touch a desktop."""


class ClipboardUnavailable(Exception):
    """No tool on this machine can read an image from the clipboard."""


def read_clipboard_image(
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    run: RunCommand | None = None,
    which: Callable[[str], str | None] | None = None,
) -> bytes | None:
    """The image on the clipboard, or ``None`` when there is none.

    Args:
        environ: Environment to read the session type from (``os.environ``).
        platform: ``sys.platform`` by default.
        run: Command runner. Defaults to `subprocess`.
        which: `shutil.which`, injected so the tests can declare what is installed.

    Raises:
        ClipboardUnavailable: When the desktop cannot be read at all. The caller
            turns this into one line in the chat; a missing tool is not a crash.
    """
    env = os.environ if environ is None else environ
    system = sys.platform if platform is None else platform
    runner = subprocess_run if run is None else run
    finder = shutil.which if which is None else which

    if system == 'darwin':
        return _read_macos(runner, finder)
    if system.startswith('win'):
        return _read_windows(runner)
    return _read_linux(runner, finder, env)


# --- Linux ------------------------------------------------------------------


def _read_linux(runner: RunCommand, which: Callable[[str], str | None], env: Mapping[str, str]) -> bytes | None:
    """Wayland first, then X11.

    `XDG_SESSION_TYPE` is the declaration; the socket variables are the evidence.
    A Wayland session that exports `DISPLAY` (because XWayland runs) still owns the
    clipboard it wrote, so the evidence wins over the declaration only when the
    declaration is missing.
    """
    session = (env.get('XDG_SESSION_TYPE') or '').lower()
    on_wayland = session == 'wayland' or (not session and bool(env.get('WAYLAND_DISPLAY')))

    if on_wayland:
        if which('wl-paste') is None:
            raise ClipboardUnavailable(
                'Reading an image from the clipboard needs `wl-paste`, which is not installed. '
                'Install `wl-clipboard` to paste screenshots.'
            )
        return _read_wayland(runner)

    if which('xclip') is None:
        raise ClipboardUnavailable(
            'Reading an image from the clipboard needs `xclip`, which is not installed. '
            'Install it to paste screenshots.'
        )
    return _read_x11(runner)


def _read_wayland(runner: RunCommand) -> bytes | None:
    """`wl-paste` lists the offered types before handing over the bytes."""
    listed = runner(['wl-paste', '--list-types'], TIMEOUT_SECONDS)
    if not listed.ok:
        return None
    image_type = _pick_image_type(listed.stdout.decode('utf-8', 'replace').split())
    if image_type is None:
        return None
    payload = runner(['wl-paste', '--type', image_type], TIMEOUT_SECONDS)
    return payload.stdout if payload.ok and payload.stdout else None


def _read_x11(runner: RunCommand) -> bytes | None:
    """`xclip` exposes the offered targets the same way."""
    listed = runner(['xclip', '-selection', 'clipboard', '-t', 'TARGETS', '-o'], TIMEOUT_SECONDS)
    if not listed.ok:
        return None
    image_type = _pick_image_type(listed.stdout.decode('utf-8', 'replace').split())
    if image_type is None:
        return None
    payload = runner(['xclip', '-selection', 'clipboard', '-t', image_type, '-o'], TIMEOUT_SECONDS)
    return payload.stdout if payload.ok and payload.stdout else None


PNG_TYPE = 'image/png'
"""Preferred over every other type: it is lossless and already a format providers accept."""

_TYPE_PREFERENCE = (PNG_TYPE, 'image/jpeg', 'image/bmp', 'image/tiff', 'image/webp', 'image/gif')
"""Accepted types, in the order the bytes are asked for.

`image/bmp` is here because some Linux toolkits offer a screenshot only as BMP;
`prepare_image` re-encodes it, which is cheaper than refusing it.
"""


def _pick_image_type(offered: Sequence[str]) -> str | None:
    """The first accepted type the clipboard offers, or ``None``.

    A clipboard that offers `image/png` and `image/tiff` gets asked for the PNG:
    the same picture, half the bytes, and no transcoding on the far side.
    """
    available = {item.strip() for item in offered}
    for candidate in _TYPE_PREFERENCE:
        if candidate in available:
            return candidate
    for item in sorted(available):
        if item.startswith(IMAGE_TYPE_PREFIX):
            return item
    return None


# --- macOS and Windows ------------------------------------------------------

MACOS_TOOL = 'pngpaste'
"""The one reliable way to get image bytes out of the macOS pasteboard."""


def _read_macos(runner: RunCommand, which: Callable[[str], str | None]) -> bytes | None:
    """`pngpaste -` writes the clipboard image as PNG on stdout."""
    if which(MACOS_TOOL) is None:
        raise ClipboardUnavailable(
            'Reading an image from the clipboard needs `pngpaste`, which is not installed. '
            'Install it with `brew install pngpaste` to paste screenshots.'
        )
    payload = runner([MACOS_TOOL, '-'], TIMEOUT_SECONDS)
    return payload.stdout if payload.ok and payload.stdout else None


WINDOWS_SCRIPT = (
    'Add-Type -AssemblyName System.Windows.Forms;'
    '$img=[System.Windows.Forms.Clipboard]::GetImage();'
    'if($img -ne $null){'
    '$out=Join-Path $env:TEMP ("sigil-paste-"+[guid]::NewGuid().ToString()+".png");'
    '$img.Save($out,[System.Drawing.Imaging.ImageFormat]::Png);'
    '[Console]::Out.Write($out)}'
)
"""Saves the clipboard image to a temporary PNG and prints the path.

`Add-Type -AssemblyName System.Drawing` is implicit through Windows Forms, which
already loads it; loading it twice is what makes the script slow.
"""


def _read_windows(runner: RunCommand) -> bytes | None:
    """PowerShell writes the clipboard image to a temp file; the bytes come back from there."""
    result = runner(['powershell', '-NoProfile', '-Command', WINDOWS_SCRIPT], TIMEOUT_SECONDS * 3)
    if not result.ok:
        return None
    path = result.stdout.decode('utf-8', 'replace').strip()
    if not path:
        return None
    try:
        with open(path, 'rb') as handle:
            return handle.read()
    except OSError:
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# --- the default runner -----------------------------------------------------


def subprocess_run(args: Sequence[str], timeout: float) -> CommandResult:
    """Runs ``args`` and captures its bytes without a shell.

    No shell on purpose: the arguments are fixed strings, and a clipboard tool that
    is fed a shell would be one more place where a filename could be read as a
    command.
    """
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # A tool that hangs or vanished is "no image", not an error the user has
        # to read: the text paste is still there.
        return CommandResult(returncode=1, stdout=b'', stderr=str(exc))
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr.decode('utf-8', 'replace'),
    )
