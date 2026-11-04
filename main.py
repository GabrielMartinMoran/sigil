import os

import rich

from src.engine.shell.guard import SELF_GUARD_ENV
from src.sigil import run


def main() -> None:
    # Self-protection: if we are being launched as a child of the `run` tool,
    # refuse to start another Sigil instance (avoids recursion/deadlocks).
    if os.environ.get(SELF_GUARD_ENV) == '1':
        raise SystemExit(
            f'Refusing to start Sigil: detected nested invocation ({SELF_GUARD_ENV}=1). '
            'Run Sigil directly from a terminal, not from inside the agent.'
        )

    run()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        rich.print('\n[bold red]Exiting Sigil...[/bold red]')
