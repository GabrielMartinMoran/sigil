import os
import sys

import rich

from src.engine.shell.guard import SELF_GUARD_ENV
from src.sigil import run

SERVER_COMMAND = 'server'
"""First argument that selects the web server instead of the terminal client."""


def main() -> None:
    # Self-protection: if we are being launched as a child of the `run` tool,
    # refuse to start another Sigil instance (avoids recursion/deadlocks).
    if os.environ.get(SELF_GUARD_ENV) == '1':
        raise SystemExit(
            f'Refusing to start Sigil: detected nested invocation ({SELF_GUARD_ENV}=1). '
            'Run Sigil directly from a terminal, not from inside the agent.'
        )

    # `sigil server` is a subcommand, not a workspace named "server": it is checked
    # before anything else touches the arguments, and it is the only one there is.
    if len(sys.argv) > 1 and sys.argv[1] == SERVER_COMMAND:
        from src.server.cli import run as run_server

        raise SystemExit(run_server(sys.argv[2:]))

    run()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        rich.print('\n[bold red]Exiting Sigil...[/bold red]')
