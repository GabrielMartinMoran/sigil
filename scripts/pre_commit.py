"""The pre-commit gate: format, lint, and keep the committed bundle honest.

Why a Python module and not a shell script: the hook has to decide *which*
toolchain applies to *which* staged file, run three of them, re-stage what they
rewrote, and rebuild `web/dist` when the sources moved. That is logic, it has edge
cases, and it is worth testing without a terminal (`tests/test_pre_commit.py`).

The contract, in order:

1. **Only what is staged is touched.** A file that is not part of the commit must
   not change in shape because someone ran `git commit`.
2. **Fixing is the default, failing is the exception.** `ruff format`,
   `prettier --write` and `--fix` on both linters run and their output is
   re-staged. The commit stops only when a rule cannot be fixed automatically, and
   the message then names the file and the rule.
3. **The bundle is rebuilt when the sources changed.** `web/dist` is committed so
   the server needs no Node; the hook is what makes that safe to do.
4. **Every failure says how to go forward.** Missing toolchain, a list of lint
   errors, a build that broke: each one ends with the command that unblocks it.

`SKIP_SIGIL_HOOK=1` skips everything (for a rebase or a WIP commit). The narrower
`SKIP_WEB_BUILD` / `SKIP_WEB_LINT` / `SKIP_PY_LINT` exist because a user hitting a
missing Node should not have to disable the Python side too.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

WEB_DIR = 'web'
"""Directory of the web client, relative to the repository root."""

WEB_IGNORED_PREFIXES = ('web/dist/', 'web/node_modules/')
"""Changes here are output or dependencies: they neither need linting nor a rebuild."""

PYTHON_SUFFIXES = ('.py',)
"""Files ruff formats and lints."""

WEB_SUFFIXES = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs', '.css', '.json', '.html')
"""Files prettier formats and eslint lints (eslint only sees the script ones)."""

ESLINT_SUFFIXES = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs')
"""Files eslint can parse. CSS, JSON and HTML go through prettier only."""

MAX_ESLINT_FILES = 200
"""Past this many files, lint the tree instead of the list: a command line has limits."""

SKIP_ALL = 'SKIP_SIGIL_HOOK'
SKIP_PY = 'SKIP_PY_LINT'
SKIP_WEB = 'SKIP_WEB_LINT'
SKIP_BUILD = 'SKIP_WEB_BUILD'


class HookError(RuntimeError):
    """A step failed in a way the user has to resolve. The message is the whole story."""


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, '').strip().lower() in {'1', 'true', 'yes', 'on'}


# --- what applies to what ---------------------------------------------------


def python_files(files: list[str]) -> list[str]:
    """Staged files ruff owns."""
    return [name for name in files if name.endswith(PYTHON_SUFFIXES)]


def web_files(files: list[str]) -> list[str]:
    """Staged files prettier owns: the web client's sources and configs."""
    return [
        name
        for name in files
        if name.startswith(f'{WEB_DIR}/') and name.endswith(WEB_SUFFIXES) and not name.startswith(WEB_IGNORED_PREFIXES)
    ]


def eslint_files(files: list[str]) -> list[str]:
    """Staged files eslint owns: the require-parseable subset."""
    return [name for name in web_files(files) if name.endswith(ESLINT_SUFFIXES)]


def sources_changed(files: list[str]) -> bool:
    """Whether the bundle must be rebuilt for this commit.

    Anything under `web/` that is not the bundle itself or the installed packages
    is an input, including a lockfile bump: the same sources can build differently
    against another toolchain, and the manifest would then describe the old one.
    """
    return any(name.startswith(f'{WEB_DIR}/') and not name.startswith(WEB_IGNORED_PREFIXES) for name in files)


# --- running things ---------------------------------------------------------


def _find_ruff(root: Path) -> str | None:
    """The project's ruff: the venv first, so the hook uses the pinned version."""
    for candidate in (root / '.venv' / 'bin' / 'ruff', root / '.venv' / 'Scripts' / 'ruff.exe'):
        if candidate.is_file():
            return str(candidate)
    return shutil.which('ruff')


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def _format_python(root: Path, ruff: str, files: list[str]) -> None:
    """`ruff format` then `ruff check --fix`, both limited to the staged files."""
    for command in ([ruff, 'format', '--force-exclude'], [ruff, 'check', '--fix', '--force-exclude']):
        result = _run([*command, *files], cwd=root)
        if result.returncode != 0:
            raise HookError(
                f'ruff could not fix the Python files in this commit:\n{result.stdout}{result.stderr}'.strip()
                + '\n\nFix them, or commit with SKIP_PY_LINT=1.'
            )


def _npm(root: Path) -> str:
    npm = shutil.which('npm')
    if npm is None:
        raise HookError(
            'npm was not found, so the web client cannot be formatted, linted or built.\n'
            'Install Node.js, or commit with SKIP_WEB_LINT=1 SKIP_WEB_BUILD=1 if you did not touch web/.'
        )
    return npm


def _node_modules(root: Path) -> Path:
    return root / WEB_DIR / 'node_modules'


def _format_web(root: Path, npm: str, files: list[str]) -> None:
    """`prettier --write` on the staged files, then `eslint --fix` on the scripts."""
    if not _node_modules(root).is_dir():
        raise HookError(
            'web/node_modules is missing, so the web client cannot be formatted or linted.\n'
            f'Run `npm ci` in {WEB_DIR}/, or commit with SKIP_WEB_LINT=1.'
        )
    relative = [str(Path(name).relative_to(WEB_DIR)) for name in files]
    result = _run([npm, 'exec', '--no', '--', 'prettier', '--write', '--ignore-unknown', *relative], cwd=root / WEB_DIR)
    if result.returncode != 0:
        raise HookError(f'prettier failed:\n{result.stdout}{result.stderr}'.strip())

    scripts = [str(Path(name).relative_to(WEB_DIR)) for name in eslint_files(files)]
    if not scripts:
        return
    targets = scripts if len(scripts) <= MAX_ESLINT_FILES else ['.']
    result = _run([npm, 'exec', '--no', '--', 'eslint', '--fix', *targets], cwd=root / WEB_DIR)
    if result.returncode != 0:
        raise HookError(
            f'eslint found problems it cannot fix:\n{result.stdout}{result.stderr}'.strip()
            + f'\n\nFix them in {WEB_DIR}/, or commit with SKIP_WEB_LINT=1.'
        )
    # A second prettier pass: `eslint --fix` can reflow what it rewrites.
    _run([npm, 'exec', '--no', '--', 'prettier', '--write', '--ignore-unknown', *scripts], cwd=root / WEB_DIR)


def _rebuild_bundle(root: Path) -> str:
    """Rebuilds `web/dist` and returns the new hash."""
    sys.path.insert(0, str(root))
    from src.server import webbuild

    try:
        return webbuild.build()
    except webbuild.BuildError as exc:
        raise HookError(
            f'{exc}\n\nFix the build, or commit with SKIP_WEB_BUILD=1 (the bundle will then be stale).'
        ) from exc


# --- git --------------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    result = _run(['git', *args], cwd=root)
    if result.returncode != 0:
        raise HookError(f'git {" ".join(args)} failed:\n{result.stderr}'.strip())
    return result.stdout


def staged_files(root: Path) -> list[str]:
    """Paths of the added or modified files in the index, relative to the root."""
    output = _git(root, 'diff', '--cached', '--name-only', '--diff-filter=ACMR')
    return [line for line in output.splitlines() if line.strip()]


def repo_root() -> Path:
    """The top level of the worktree. The hook may be invoked from a subdirectory."""
    result = _run(['git', 'rev-parse', '--show-toplevel'], cwd=Path.cwd())
    if result.returncode != 0:
        raise HookError('this is not a git repository.')
    return Path(result.stdout.strip())


def _restage(root: Path, files: list[str]) -> None:
    """Puts the reformatted files back in the index: the commit takes the fixed version."""
    if not files:
        return
    existing = [name for name in files if (root / name).is_file()]
    if existing:
        _git(root, 'add', '--', *existing)


# --- the whole gate ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Report:
    """What the hook did, so a caller (the CLI, a test) can assert on it.

    ``checked`` is how many files a tool looked at and ``rewritten`` how many came
    back different: a commit that touched one Python file must not report having
    formatted four hundred.
    """

    checked: tuple[str, ...] = ()
    rewritten: tuple[str, ...] = ()
    rebuilt: str | None = None
    skipped: str = ''


def _snapshot(root: Path, files: list[str]) -> dict[str, bytes]:
    """The bytes of each file before a formatter runs, to know what actually changed."""
    snapshot: dict[str, bytes] = {}
    for name in files:
        try:
            snapshot[name] = (root / name).read_bytes()
        except OSError:
            continue
    return snapshot


def _changed(root: Path, before: dict[str, bytes]) -> list[str]:
    """The files a formatter rewrote, by comparing against the snapshot."""
    changed = []
    for name, previous in before.items():
        try:
            if (root / name).read_bytes() != previous:
                changed.append(name)
        except OSError:
            continue
    return changed


def run(root: Path | None = None, *, files: list[str] | None = None) -> Report:
    """Formats, lints and rebuilds what this commit needs.

    Raises:
        HookError: When a step cannot be fixed automatically. The message is meant
            to be printed verbatim: it names the tool and the way out.
    """
    if _env_truthy(SKIP_ALL):
        return Report(skipped=f'{SKIP_ALL} is set')
    base = root if root is not None else repo_root()
    touched = staged_files(base) if files is None else files
    checked: list[str] = []
    rewritten: list[str] = []

    py_files = python_files(touched)
    if py_files and not _env_truthy(SKIP_PY):
        ruff = _find_ruff(base)
        if ruff is None:
            raise HookError('ruff was not found. Run `uv sync` in the repository, or commit with SKIP_PY_LINT=1.')
        before = _snapshot(base, py_files)
        _format_python(base, ruff, py_files)
        checked.extend(py_files)
        rewritten.extend(_changed(base, before))

    web = web_files(touched)
    if web and not _env_truthy(SKIP_WEB):
        before = _snapshot(base, web)
        _format_web(base, _npm(base), web)
        checked.extend(web)
        rewritten.extend(_changed(base, before))

    _restage(base, rewritten)

    rebuilt: str | None = None
    if sources_changed(touched) and not _env_truthy(SKIP_BUILD):
        rebuilt = _rebuild_bundle(base)
        _git(base, 'add', '--', f'{WEB_DIR}/dist')

    return Report(checked=tuple(checked), rewritten=tuple(rewritten), rebuilt=rebuilt)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='scripts/pre_commit.py',
        description='Formats, lints and rebuilds the web bundle for the files in this commit.',
    )
    parser.add_argument('--files', nargs='*', help='check these paths instead of the index (testing and CI)')
    parser.add_argument('--all', action='store_true', help='check every tracked file, not just the index')
    args = parser.parse_args(argv)

    files = args.files
    if args.all:
        files = _git(repo_root(), 'ls-files').splitlines()
    try:
        report = run(files=files)
    except HookError as exc:
        print(f'\nsigil pre-commit: {exc}\n', file=sys.stderr)
        return 1

    if report.skipped:
        print(f'sigil pre-commit: skipped ({report.skipped})')
        return 0
    if report.rewritten:
        print(f'sigil pre-commit: rewrote {len(report.rewritten)} file(s) and re-staged them')
    elif report.checked:
        print(f'sigil pre-commit: {len(report.checked)} file(s) already clean')
    if report.rebuilt:
        print(f'sigil pre-commit: rebuilt web/dist ({report.rebuilt})')
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())


__all__ = [
    'HookError',
    'Report',
    'eslint_files',
    'python_files',
    'repo_root',
    'run',
    'sources_changed',
    'staged_files',
    'web_files',
]
