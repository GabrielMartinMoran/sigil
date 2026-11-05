"""Parsing of the `/new` arguments.

`/new` alone keeps the old behaviour: a fresh session in the current workspace.
`--worktree <name>` creates a worktree and starts the session there. The parsing
is separated from the command so the accepted forms can be tested without a UI.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

WORKTREE_FLAGS = frozenset({'-w', '--worktree'})

USAGE = (
    'Usage: /new [--worktree <name>]\n'
    '  /new                     new session in the current workspace\n'
    '  /new --worktree fix-x    create a worktree and start the session there'
)


@dataclass(frozen=True, slots=True)
class NewSessionRequest:
    """What `/new` has to do."""

    worktree: str | None = None


class UsageError(ValueError):
    """The arguments do not match the accepted forms of the command."""


def parse_new_args(args: str) -> NewSessionRequest:
    """Parses ``/new`` arguments.

    Accepted forms: empty, ``--worktree <name>`` and ``--worktree=<name>`` (with
    ``-w`` as the short alias). Anything else is a usage error instead of a silent
    new session: a typo in the flag must not look like it worked.
    """
    text = args.strip()
    if not text:
        return NewSessionRequest()

    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise UsageError(f'{USAGE}\n({exc})') from exc

    slug: str | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in WORKTREE_FLAGS:
            index += 1
            if index >= len(tokens):
                raise UsageError(f'`{token}` needs a worktree name.\n{USAGE}')
            slug = _single(slug, tokens[index])
            index += 1
            continue
        if token.startswith(('--worktree=', '-w=')):
            slug = _single(slug, token.partition('=')[2])
            index += 1
            continue
        raise UsageError(f'Unknown argument {token!r}.\n{USAGE}')

    return NewSessionRequest(worktree=slug)


def _single(current: str | None, value: str) -> str:
    if current is not None:
        raise UsageError(f'Only one worktree per session.\n{USAGE}')
    if not value.strip():
        raise UsageError(f'The worktree name cannot be empty.\n{USAGE}')
    return value
