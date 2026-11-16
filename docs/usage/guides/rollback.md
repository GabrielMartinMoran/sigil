# Roll back or fork a conversation

Use `/rollback` to return to **before a selected user turn**. Choose carefully:
rollback can change both the saved conversation and workspace files, including
edits you made yourself after that point.

## Choose the right action

A fork preserves the original conversation; a rollback rewinds it:

| Action                      | Conversation                                                                | Workspace files                 |
| --------------------------- | --------------------------------------------------------------------------- | ------------------------------- |
| Roll back with files        | Removes the selected turn and everything after it                           | Restores the available snapshot |
| Roll back conversation only | Removes the selected turn and everything after it                           | Unchanged                       |
| Fork                        | Creates a new session from the earlier conversation; preserves the original | Unchanged                       |

A fork isn't a separate Git branch. Use a
[worktree](sessions.md#work-on-a-separate-branch) when you also need an
independent set of files.

## Go back to a turn

Wait for work to finish or [stop the turn](conversations.md#stop-a-turn) first.
Then use the terminal flow:

1. Run `/rollback` and select the turn to return to. Clicking a user message
   opens the same choice; it doesn't immediately undo anything.
2. Inspect the actions and file-change summary.
3. Choose conversation-only rollback, rollback with files, or a fork.
4. Confirm the intended operation.

The point is before the selected message, not after its answer. Messages sent as
corrections within a running turn aren't separate rollback points. After a
rollback, the original prompt and images return to the composer for editing.

## Understand the limits before restoring files

File rollback is available through Git-backed snapshots. Outside a Git
repository, conversational rollback still works but file restore is unavailable.

- The restore covers the workspace snapshot, not only edits made by Sigil. It
  can revert your later manual edits and remove files created after the point.
- Ignored files aren't captured or restored. Rolling back doesn't reinstall
  dependencies or undo changes in ignored build directories.
- Files outside the workspace aren't restored.
- External actions aren't undone: a pushed commit, sent message, or API call
  remains an external effect.
- Filesystem restoration isn't a database transaction. A failed restore can
  leave partially restored files while the conversation remains intact.

Sigil records recovery information before destructive steps, but that isn't a
substitute for reviewing the change or maintaining your own backups. Stop and
inspect any reported restore failure before retrying unrelated work.

For snapshot retention, recovery records, and failure ordering, read the
[rollback implementation reference](../../technical/internals/rollback.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
