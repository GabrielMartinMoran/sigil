import os

import rich

from src.engine.tools.tool_manager import SELF_GUARD_ENV
from src.sigil import run


def main() -> None:
    # Autoprotección: si estamos siendo arrancados como hijo de la tool `run`,
    # negarnos a iniciar otra instancia de Sigil (evita recursión/bloqueos).
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
