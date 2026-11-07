"""Installs the repository's git hooks.

A hook kept in `.git/hooks` is invisible: it does not travel with a clone, it is
not reviewed, and `git clean` eats it. `.githooks/` plus `core.hooksPath` makes it
a tracked file instead.

`core.hooksPath` itself is still per-clone configuration — git has no way to ship
it inside the repository — so something has to run once per clone. Two entry points
do it without anyone having to remember:

- **`./sigil`** (the launcher) calls `ensure()` on every start. It is a `git config`
  read in the common case, and a clone that has never run Sigil still gets the hook
  the first time it is launched. `SIGIL_NO_HOOKS=1` opts out.
- **`npm ci` / `npm install`** in `web/` runs `web/scripts/install-hooks.mjs` via
  `prepare`, which covers the clone where someone goes straight to the client.

`install()` is the manual, verbose path: it also explains what is missing (no npm,
a hook without the executable bit). `ensure()` is the silent one used by the entry
points, and it only reports when it actually changed something.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = '.githooks'
HOOK_NAMES = ('pre-commit',)

OPT_OUT = 'SIGIL_NO_HOOKS'
"""Set to anything truthy to leave the clone's git configuration alone."""


def repo_root(cwd: Path | None = None) -> Path:
    """The top level of the worktree this interpreter's code lives in."""
    result = subprocess.run(
        ['git', 'rev-parse', '--show-toplevel'],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit('install_hooks: this is not a git repository.')
    return Path(result.stdout.strip())


def project_root() -> Path:
    """The checkout Sigil's own code lives in, found from this file, not from the cwd.

    The working directory is the *workspace* — the project Sigil was opened on — and
    it is usually a different repository. Installing hooks there would be wrong.
    """
    return Path(__file__).resolve().parents[1]


def _current_hooks_path(root: Path) -> str:
    result = subprocess.run(
        ['git', 'config', '--get', 'core.hooksPath'],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ''


def _is_checkout(root: Path) -> bool:
    """Whether ``root`` is the top of a git worktree, without shelling out to git."""
    return (root / '.git').exists()


def is_installed(root: Path | None = None) -> bool:
    """Whether this clone already points at the tracked hooks."""
    base = root if root is not None else project_root()
    return _is_checkout(base) and _current_hooks_path(base) == HOOKS_DIR


def _opted_out() -> bool:
    return os.environ.get(OPT_OUT, '').strip().lower() in {'1', 'true', 'yes', 'on'}


def install(root: Path | None = None) -> list[str]:
    """Points `core.hooksPath` at `.githooks` and makes every hook executable."""
    base = root if root is not None else project_root()
    directory = base / HOOKS_DIR
    if not directory.is_dir():
        raise SystemExit(f'install_hooks: {directory} is missing.')

    messages: list[str] = []
    previous = _current_hooks_path(base)
    if previous != HOOKS_DIR:
        subprocess.run(['git', 'config', 'core.hooksPath', HOOKS_DIR], cwd=base, check=True, capture_output=True)
        messages.append(f'core.hooksPath: {previous or "(unset)"} -> {HOOKS_DIR}')

    for name in HOOK_NAMES:
        hook = directory / name
        if not hook.is_file():
            messages.append(f'{HOOKS_DIR}/{name} is missing, skipped')
            continue
        mode = hook.stat().st_mode
        if not mode & stat.S_IXUSR:
            hook.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            messages.append(f'{HOOKS_DIR}/{name}: made executable')

    npm = shutil.which('npm')
    if npm is None:
        messages.append('npm was not found: the web half of the hook will ask for Node when it runs')
    else:
        messages.append('npm found: web formatting, linting and the bundle rebuild will run')
    return messages


def ensure(root: Path | None = None) -> bool:
    """Installs the hooks if this clone does not have them yet.

    Returns ``True`` when something changed, so a caller can announce it once and
    stay silent on every later start. Never raises: a checkout without git, a
    read-only config or a missing `.githooks` must not stop Sigil from running.
    """
    if _opted_out():
        return False
    base = root if root is not None else project_root()
    try:
        if not _is_checkout(base) or is_installed(base):
            return False
        install(base)
    except OSError, RuntimeError, SystemExit, subprocess.SubprocessError:
        return False
    return True


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='scripts/install_hooks.py',
        description='Installs the tracked git hooks (format, lint, rebuild web/dist).',
    )
    parser.add_argument('--root', type=Path, help='repository root, when it is not the checkout holding this file')
    parser.add_argument('--quiet', action='store_true', help='say nothing when the hooks were already installed')
    args = parser.parse_args(argv)

    base = args.root if args.root is not None else project_root()
    if not _is_checkout(base):
        if not args.quiet:
            print(f'  {base} is not a git checkout, nothing to install')
        return 0
    if args.quiet and is_installed(base):
        return 0
    for line in install(base):
        print(f'  {line}')
    print('hooks installed.')
    return 0


if __name__ == '__main__':
    sys.exit(_main())


__all__ = ['HOOKS_DIR', 'HOOK_NAMES', 'OPT_OUT', 'ensure', 'install', 'is_installed', 'project_root', 'repo_root']
