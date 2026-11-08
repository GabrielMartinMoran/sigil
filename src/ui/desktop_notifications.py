"""Best-effort desktop attention signals, separate from in-app toasts.

Project and conversation titles may appear on a locked screen. Prompts, model
output and exception messages must never be included.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from src.ui.window_title import compose

DELIVERY_TIMEOUT = 3


class Attention(StrEnum):
    FINISHED = 'Turn finished. Your response is ready.'
    FAILED = 'Turn failed. Open Sigil for details.'
    QUESTION = 'A question is waiting for your answer.'


@dataclass(frozen=True, slots=True)
class NotificationContext:
    project: str = ''
    conversation: str = ''

    @property
    def title(self) -> str:
        return compose(self.project, self.conversation) or 'Sigil'


EMPTY_CONTEXT = NotificationContext()


def notification_command(
    event: Attention,
    context: NotificationContext = EMPTY_CONTEXT,
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str] | None:
    """Selects the local desktop backend; no shell or user-controlled arguments."""
    system = sys.platform if platform is None else platform
    if system.startswith('linux') and (executable := which('notify-send')):
        return [executable, '--app-name=Sigil', '--hint=string:desktop-entry:sigil', context.title, event.value]
    if system == 'darwin' and (executable := which('osascript')):
        return [
            executable,
            '-e',
            'on run argv\n display notification (item 2 of argv) with title (item 1 of argv)\nend run',
            context.title,
            event.value,
        ]
    return None


def send_notification(event: Attention, context: NotificationContext = EMPTY_CONTEXT) -> bool:
    """Attempts delivery without allowing a missing or hung desktop to fail the UI."""
    command = notification_command(event, context)
    if command is None:
        return False
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=DELIVERY_TIMEOUT,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return result.returncode == 0
