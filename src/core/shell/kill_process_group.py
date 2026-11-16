"""The kill_process_group helper of the core layer."""

from __future__ import annotations

import os
import signal
import subprocess


def kill_process_group(process: subprocess.Popen[bytes]) -> None:
    """Terminates the whole process group of the command."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        if process.poll() is None:
            process.kill()
