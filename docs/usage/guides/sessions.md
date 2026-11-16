# Sessions, workspaces, and worktrees

A session is a saved conversation. A workspace is the directory its tools work
in. A Git worktree is a separate working directory for the same repository. Use
sessions to separate conversations and worktrees to separate file changes.

## Start or resume a conversation

Use `/new` for an empty conversation or `/load` to choose a saved one. The list
includes sessions from the repository's workspaces and labels their worktrees.
Selecting a session from another worktree also selects that workspace, so it
isn't opened against the wrong files.

A saved session restores its agent, model selection, conversation, roadmap, and
recorded subagents. If an account or profile no longer exists, Sigil reports or
resolves the missing choice rather than recreating its credentials.

In the default service-backed terminal, selecting another session changes the
client view without cancelling work still running in the previous session.
Switching to another workspace can still be refused when it would conflict with
active work. Use the explicit stop action when you want cancellation.

Sessions are stored in the SQLite database under `SIGIL_HOME`, not as editable
`<id>.json` conversation files. A removed Git worktree's sessions aren't offered
as workspaces you can still open.

## Recognize a saved session

After the first message, Sigil asks the conversation's model for a short title
in a separate background request. This can add provider usage; it doesn't block
the main response. The title appears in `/load` and, when supported, the
terminal window title alongside the repository or workspace name.

If naming fails, the conversation still works and the picker falls back to the
first message. A terminal that ignores window-title updates keeps its own title.

## Work on a separate branch

From a Git repository, create a named worktree and a new session with:

```text
/new --worktree fix-login
```

Sigil creates branch `sigil/fix-login` from `HEAD`, creates the worktree outside
the repository, and selects it for the new session. `-w` is the short form. The
default parent directory is `<SIGIL_HOME>/worktrees`; set `SIGIL_WORKTREES_DIR`
at startup to choose another location.

A worktree starts from committed files. It doesn't copy uncommitted changes or
ignored files such as environment files, virtual environments, and an ignored
project `.sigil/config.json`. Prepare any required local dependencies yourself.

MCP configuration is loaded at startup rather than automatically replaced for
each worktree switch. Don't assume a new worktree installs another tool setup.

## Know which files you are changing

Read the worktree and branch in the terminal status bar. `/context` also reports
the workspace root. Starting in a repository subdirectory is supported; that
subdirectory is its own workspace for session purposes.

File tools normally stay inside the active workspace and temporary directories.
That isn't a shell sandbox. Review [Permissions and safety](safety.md) before
working across multiple trees.

## Next steps

Use [Rollback and forks](rollback.md) to revisit a point within a conversation.
A conversation fork doesn't create a Git worktree or restore files. For
identities and persistence, read the
[technical domain model](../../technical/architecture/domain.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
