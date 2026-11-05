"""Discovery and creation of the worktrees of a git repository.

Everything goes through the guarded `run` machinery (`execute_command`): the
commands are non-interactive, bounded and inherit the same environment policy as
the agent's shell. No credentials are read and no prompt is possible
(``GIT_TERMINAL_PROMPT=0``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from src.engine.shell.models.run_tool_output import RunToolOutput
from src.engine.shell.runner import execute_command
from src.engine.workspace.models.workspace_error import WorkspaceError
from src.engine.workspace.models.worktree import Worktree
from src.engine.workspace.naming import branch_for, worktree_path

GIT_TIMEOUT = 30
"""Upper bound of a git call: `worktree add` on a big repository is still fast."""

_GIT_ENV = {'GIT_OPTIONAL_LOCKS': '0'}
"""Read-only plumbing must not write the index lock while Sigil inspects the repo."""


def _git(args: list[str], cwd: Path) -> RunToolOutput:
    command = 'git ' + ' '.join(_quote(arg) for arg in args)
    return execute_command(command, GIT_TIMEOUT, cwd=cwd, extra_env=_GIT_ENV, check_self_invocation=False)


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _failure(action: str, result: RunToolOutput) -> WorkspaceError:
    detail = (result.stderr or result.stdout).strip() or f'exit code {result.returncode}'
    return WorkspaceError(f'{action}: {detail}')


@dataclass(frozen=True, slots=True)
class GitRepository:
    """A git repository, with the worktree Sigil is currently looking from."""

    toplevel: Path
    """Root of the worktree Sigil is looking from: the main one or a linked one."""

    common_dir: Path
    """The shared ``.git``: the same for the main worktree and every linked one."""

    @property
    def main_root(self) -> Path:
        """Root of the main worktree, which is the one that owns the shared ``.git``.

        It cannot be taken from ``toplevel``: inside a linked worktree, git answers
        with the worktree itself, which would make every worktree look like the main
        one.
        """
        if self.common_dir.name == '.git':
            return self.common_dir.parent
        # A bare repository (or a git dir with a custom name) has no worktree that
        # clearly owns it; the current root is the best available answer.
        return self.toplevel

    @property
    def name(self) -> str:
        """Repository name: the directory of the main worktree."""
        return self.main_root.name or self.toplevel.name

    # --- discovery ---------------------------------------------------------

    @classmethod
    def discover(cls, cwd: str | os.PathLike[str] | None = None) -> GitRepository | None:
        """The repository that contains ``cwd``, or ``None`` outside a work tree."""
        base = Path(cwd) if cwd is not None else Path.cwd()
        result = _git(['rev-parse', '--show-toplevel', '--git-common-dir'], base)
        if result.returncode != 0:
            return None

        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(lines) < 2:
            return None

        toplevel = Path(lines[0])
        common_dir = Path(lines[1])
        if not common_dir.is_absolute():
            # In the main worktree git answers `.git`, relative to the toplevel.
            common_dir = toplevel / common_dir
        return cls(toplevel=Path(os.path.realpath(toplevel)), common_dir=Path(os.path.realpath(common_dir)))

    def worktrees(self) -> list[Worktree]:
        """Every worktree of the repository, main first, as git lists them.

        A repository with no worktrees registered answers with the main one
        only, which keeps the callers free of the empty case.
        """
        result = _git(['worktree', 'list', '--porcelain'], self.toplevel)
        if result.returncode != 0:
            raise _failure('Cannot list the worktrees', result)

        # git lists the main worktree first, but its path is the authority: the
        # order alone would mislabel a repository whose main worktree is gone.
        return [
            Worktree(is_main=Path(os.path.realpath(str(fields['path']))) == self.main_root, **fields)
            for fields in self._parse_porcelain(result.stdout)
        ]

    def worktree_for(self, path: str | os.PathLike[str]) -> Worktree | None:
        """Worktree registered exactly at ``path``."""
        real = Path(os.path.realpath(str(path)))
        for worktree in self.worktrees():
            if Path(os.path.realpath(str(worktree.path))) == real:
                return worktree
        return None

    def worktree_containing(self, path: str | os.PathLike[str]) -> Worktree | None:
        """Worktree whose tree contains ``path``.

        Sigil can be started in a subdirectory of a repository. That subdirectory is
        its own working directory (and therefore its own session folder), but it
        still belongs to a worktree, which is the label it has to show.
        """
        real = Path(os.path.realpath(str(path)))
        best: Worktree | None = None
        for worktree in self.worktrees():
            root = Path(os.path.realpath(str(worktree.path)))
            if real != root and not real.is_relative_to(root):
                continue
            # The deepest match wins: nested worktrees are not a thing today, but a
            # worktree inside another worktree's tree would be mislabelled otherwise.
            if best is None or len(root.parts) > len(Path(os.path.realpath(str(best.path))).parts):
                best = worktree
        return best

    @staticmethod
    def _parse_porcelain(text: str) -> list[dict]:
        """Blocks of ``key value`` separated by a blank line."""
        blocks: list[dict] = []
        current: dict = {}
        for line in text.splitlines():
            if not line.strip():
                if current:
                    blocks.append(current)
                    current = {}
                continue
            key, _, value = line.partition(' ')
            key = key.strip()
            value = value.strip()
            if key == 'worktree':
                current['path'] = Path(value)
            elif key == 'HEAD':
                current['head'] = value
            elif key == 'branch':
                current['branch'] = value.removeprefix('refs/heads/')
            elif key == 'detached':
                current['branch'] = None
            elif key == 'locked':
                current['is_locked'] = True
            elif key == 'bare':
                current['is_bare'] = True
        if current:
            blocks.append(current)
        return blocks

    # --- creation ----------------------------------------------------------

    def create_worktree(
        self,
        slug: str,
        *,
        base_dir: Path,
        start_point: str = 'HEAD',
    ) -> Worktree:
        """Creates a worktree on a new branch and returns it.

        The worktree is only created on disk: activating it is the caller's job,
        so a failure here leaves the running session untouched. Every git call runs
        in ``toplevel`` — the worktree Sigil is looking from — because that is the
        repository the new worktree belongs to; using the process cwd would create
        it in whichever repository happens to contain it.
        """
        branch = branch_for(slug)
        destination = worktree_path(base_dir, self.name, slug)

        if destination.exists():
            raise WorkspaceError(f'Worktree directory already exists: {destination}')
        if self._branch_exists(branch):
            raise WorkspaceError(f'Branch {branch!r} already exists; pick another worktree name.')

        result = _git(['worktree', 'add', '-b', branch, str(destination), start_point], self.toplevel)
        if result.returncode != 0:
            raise _failure(f'Cannot create the worktree {slug!r}', result)

        created = self.worktree_for(destination)
        if created is None:
            raise WorkspaceError(f'Worktree {slug!r} was created but git does not list it: {destination}')
        return created

    def _branch_exists(self, branch: str) -> bool:
        result = _git(['rev-parse', '--verify', '--quiet', f'refs/heads/{branch}'], self.toplevel)
        return result.returncode == 0
