"""The committed web bundle: is it current, and rebuilding it when it is not.

`web/dist` is committed so the server needs no Node to run. That buys a simple
deployment and creates exactly one failure mode: the sources change and the bundle
does not. This module is the guard, and it is the only place that knows the recipe.

- `source_hash()` digests every input. `web/scripts/source-hash.mjs` implements the
  same recipe for the build; `tests/test_web_dist.py` compares both when Node is
  available, which is what keeps the two from drifting.
- `staleness()` answers with the reason, never a bare boolean: a user who sees
  "the UI is out of date" needs to know whether to run the build or commit the result.
- `build()` shells out to npm, which is the only step that needs Node. It is never
  run implicitly by the server: a first launch that installs 300 MB of packages is
  a surprise, and a network failure would look like a broken UI.

The pre-commit hook (`scripts/pre-commit`) and `sigil serve` both call this module,
so the three entry points cannot disagree about what "fresh" means. `sigil server`
does not: the API has no bundle to be stale.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HASH_CHARS = 16
"""Characters of the digest kept in `build.json`. Must match the Node script."""

INPUT_DIRECTORIES = ('src', 'public')
"""Directories under `web/` that are build inputs."""

INPUT_FILES = (
    'index.html',
    'package.json',
    'package-lock.json',
    'vite.config.ts',
    'tsconfig.json',
    'tsconfig.app.json',
    'tsconfig.node.json',
)
"""Files under `web/` that are inputs when they exist."""

IGNORED_DIRECTORIES = frozenset({'node_modules', 'dist', '.vite'})
"""Never inputs, even under an input directory."""

MANIFEST = 'build.json'
"""Written by the Vite plugin into `dist/`."""


def web_root() -> Path:
    """The `web/` directory of this checkout."""
    return Path(__file__).resolve().parents[2] / 'web'


def dist_root(root: Path | None = None) -> Path:
    """Where the built bundle lives: what the server serves and the guard checks."""
    return (root if root is not None else web_root()) / 'dist'


def _walk(root: Path, directory: str, found: list[Path]) -> None:
    base = root / directory
    if not base.is_dir():
        return
    for entry in sorted(base.iterdir()):
        if entry.name in IGNORED_DIRECTORIES:
            continue
        if entry.is_dir():
            _walk(root, f'{directory}/{entry.name}', found)
        elif entry.is_file():
            found.append(entry)


def input_files(root: Path | None = None) -> list[Path]:
    """Every build input, sorted, with slash-separated names relative to ``web/``."""
    base = root if root is not None else web_root()
    found = [base / name for name in INPUT_FILES if (base / name).is_file()]
    for directory in INPUT_DIRECTORIES:
        _walk(base, directory, found)
    return sorted((path for path in found if path.is_file()), key=lambda path: path.relative_to(base).as_posix())


def source_hash(root: Path | None = None) -> str:
    """The digest of the inputs: `sha256` over sorted `path\\0sha256(content)` lines.

    The path is part of the input because the browser fetches by name: renaming a
    file with identical content still invalidates the bundle.
    """
    base = root if root is not None else web_root()
    digest = hashlib.sha256()
    for path in input_files(base):
        name = path.relative_to(base).as_posix()
        file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(f'{name}\0{file_digest}\n'.encode())
    return digest.hexdigest()[:HASH_CHARS]


def built_hash(root: Path | None = None) -> str | None:
    """The hash recorded by the last build, or ``None`` if the bundle was never built."""
    try:
        manifest = json.loads((dist_root(root) / MANIFEST).read_text(encoding='utf-8'))
    except OSError, ValueError:
        return None
    value = manifest.get('hash') if isinstance(manifest, dict) else None
    return value if isinstance(value, str) else None


@dataclass(frozen=True, slots=True)
class Staleness:
    """Why the bundle does not match the sources, in the terms a user acts on."""

    stale: bool
    reason: str = ''
    source_hash: str = ''
    built_hash: str | None = None

    def message(self) -> str:
        """One sentence for a terminal or a log line. Empty when fresh."""
        if not self.stale:
            return ''
        return f'{self.reason}. Run `npm run build` in web/ (or `python -m src.server.webbuild --build`).'


def staleness(root: Path | None = None) -> Staleness:
    """Whether the committed bundle matches the sources, and why not when it does not."""
    base = root if root is not None else web_root()
    current = source_hash(base)
    built = built_hash(base)
    if built is None:
        missing = 'the web bundle is not built'
        if not dist_root(base).exists():
            return Staleness(stale=True, reason=missing + ' (web/dist is missing)', source_hash=current)
        return Staleness(
            stale=True,
            reason=missing + ' (web/dist has no build.json)',
            source_hash=current,
            built_hash=None,
        )
    if built != current:
        return Staleness(
            stale=True,
            reason=f'the web bundle is out of date (built {built}, sources {current})',
            source_hash=current,
            built_hash=built,
        )
    return Staleness(stale=False, source_hash=current, built_hash=built)


class BuildError(RuntimeError):
    """npm is missing, or the build failed. The message says which."""


def _npm() -> str:
    npm = shutil.which('npm')
    if npm is None:
        raise BuildError(
            'npm was not found. Building the web client needs Node.js; the committed '
            'bundle in web/dist already works without it.'
        )
    return npm


def _run(command: list[str], cwd: Path) -> None:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        output = (result.stderr or result.stdout).strip()
        raise BuildError(f'{" ".join(command)} failed:\n{output}')


def install(root: Path | None = None) -> None:
    """Installs the toolchain, locked. Idempotent: npm exits fast when it is current."""
    base = root if root is not None else web_root()
    _run([_npm(), 'ci', '--no-audit', '--no-fund'], base)


def build(root: Path | None = None, *, install_if_needed: bool = True) -> str:
    """Builds the bundle and returns its new hash.

    ``install_if_needed`` runs `npm ci` when `node_modules` is absent, which is the
    case after a fresh clone: the alternative is a build that fails on a missing
    `vite`, and the message would blame the source code.
    """
    base = root if root is not None else web_root()
    if not (base / 'package.json').is_file():
        raise BuildError(f'{base}/package.json is missing: there is no web client to build.')
    if install_if_needed and not (base / 'node_modules').is_dir():
        install(base)
    _run([_npm(), 'run', 'build'], base)
    built = built_hash(base)
    if built is None:
        raise BuildError(f'the build finished but {MANIFEST} was not written.')
    return built


def ensure_fresh(root: Path | None = None) -> Staleness:
    """Builds when the bundle is stale. Used by the explicit rebuild paths only."""
    state = staleness(root)
    if state.stale:
        build(root)
        return staleness(root)
    return state


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='python -m src.server.webbuild',
        description='Checks and rebuilds the committed web bundle.',
    )
    parser.add_argument('--build', action='store_true', help='rebuild when stale')
    parser.add_argument('--check', action='store_true', help='exit 1 when stale (default)')
    parser.add_argument('--print-hash', action='store_true', help='print the hash of the sources')
    parser.add_argument('--json', action='store_true', help='report as JSON')
    args = parser.parse_args(argv)

    if args.print_hash:
        print(source_hash())
        return 0

    state = staleness()
    if args.build and state.stale:
        try:
            build()
        except BuildError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        state = staleness()

    if args.json:
        print(json.dumps({'stale': state.stale, 'reason': state.reason, 'hash': state.source_hash}))
    elif state.stale:
        print(state.message(), file=sys.stderr)
    else:
        print(f'web bundle is current ({state.source_hash})')
    return 1 if state.stale else 0


if __name__ == '__main__':
    raise SystemExit(_main())


__all__ = [
    'HASH_CHARS',
    'INPUT_DIRECTORIES',
    'INPUT_FILES',
    'MANIFEST',
    'BuildError',
    'Staleness',
    'build',
    'built_hash',
    'dist_root',
    'ensure_fresh',
    'input_files',
    'install',
    'source_hash',
    'staleness',
    'web_root',
]
