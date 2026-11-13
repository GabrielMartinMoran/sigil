import os
import sys

import rich

from src.engine.shell.guard import SELF_GUARD_ENV
from src.sigil import run

SURFACES = {'server': False, 'serve': True}
"""First argument that selects a server instead of the terminal client.

The value says whether that surface includes the web client: `server` is the API
alone, `serve` is the same process plus `web/dist` and the password gate. Only
`serve` can ask for a password, so the terminal client's launcher stays on `server`.
"""


def main() -> None:
    # Self-protection: if we are being launched as a child of the `run` tool,
    # refuse to start another Sigil instance (avoids recursion/deadlocks).
    if os.environ.get(SELF_GUARD_ENV) == '1':
        raise SystemExit(
            f'Refusing to start Sigil: detected nested invocation ({SELF_GUARD_ENV}=1). '
            'Run Sigil directly from a terminal, not from inside the agent.'
        )

    # `sigil server` and `sigil serve` are subcommands, not workspaces with those
    # names: they are checked before anything else touches the arguments, and they
    # are the only ones there are.
    command = sys.argv[1] if len(sys.argv) > 1 else ''
    if command in SURFACES:
        from src.server.cli import run as run_server

        raise SystemExit(run_server(sys.argv[2:], web=SURFACES[command]))

    run()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        rich.print('\n[bold red]Exiting Sigil...[/bold red]')
