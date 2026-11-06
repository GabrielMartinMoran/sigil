# Rollback: going back to a message, files included

This page designs one operation: **return a session to the moment before the
user sent a message — and make the workspace look the way it looked at that
moment.**

<!-- prettier-ignore -->
> [!IMPORTANT]
> **Designed, not built.** The schema is already in place — `checkpoints`,
> `agent_executions.created_by_turn` and `messages.turn_id` are columns of the
> database — but **nothing writes to them yet**, and the git snapshot does
> not exist. The mechanism below is measured on scratch repositories, not
> implemented.

The shape is what matters here: the tables exist so the feature is a command and
not a migration, and the snapshot is measured so its cost is known before it is
written.

Read it with [the domain](domain.md), which fixes the words, and [How Sigil
stores its state](storage.md), which owns the tables.

## The unit is the turn

A rollback point is a **`Turn` of the root execution with `origin = 'user'`**,
not a prompt and not a message.

Both restrictions matter. A prompt is not a point because steering is inside the
turn. A **subagent's** turn is not a point either: its transcript is internal to
an execution the user never sent a message to, and its files were changed as
part of the parent's turn. To undo a subagent, the user goes back to the turn
that summoned it, and the cascade takes the subagent with it.

That follows from the definition already settled: a turn is the span between the
user sending a message and the agent finishing, and **steering is part of it** —
a steer extends the prompt that opened the turn instead of opening a second one.
So "the message I sent" and "the turn" name the same instant, and a steer is not
a separate point to come back to. If it were, a session steered five times would
show five rollback points whose file state was captured mid-edit, which is
neither useful nor honest.

### Which instant

The instant restored is **just before that turn started**: the conversation as
it was when the user pressed enter, and the files as they were when the first
tool of that turn touched nothing yet.

Two consequences, both deliberate:

- **The target turn is dropped too.** It is not "keep my message and delete what
  came after"; it is "put me back where I was before I sent it", which is what a
  user does when they want to say something different. The UI labels it *go back
  to before this message*, never *delete the answer*.
- **Both halves describe the same instant.** A rollback that kept the message
  but rewound the files would leave a conversation talking about edits that no
  longer exist. One instant, two halves, or the state is a lie.

## Two halves, one command

| Half | What it does | Is it reversible? |
| --- | --- | --- |
| **Conversation** | drops the turns from the target onward, and the executions those turns created | yes: the dropped tail is exported to a blob first |
| **Files** | makes the workspace match the snapshot taken at the target turn's start | yes: a snapshot of the current tree is taken first |

The command is one call with a flag, because a user sometimes wants only one
half:

```python
def rollback_to(session_id: str, turn_id: str, *, files: bool = True) -> RollbackOutcome: ...
```

`RollbackOutcome` reports what actually happened, without optimism:

```python
@dataclass(frozen=True, slots=True)
class RollbackOutcome:
    turns_dropped: int
    executions_dropped: int
    messages_dropped: int
    files_restored: int
    files_deleted: int
    files_unavailable: bool  # True when the snapshot had no tree
    undo_checkpoint: str  # the checkpoint that can put everything back
```

## The conversation half

One transaction, and the schema does most of the work:

```sql
BEGIN IMMEDIATE;

-- Everything the dropped turns created goes with them: the FK cascade on
-- agent_executions.created_by_turn takes the subagents, their turns, their
-- messages and their plans.
DELETE FROM agent_executions
WHERE session_id = :session AND created_by_turn IN (
    SELECT id FROM turns WHERE started_at >= (SELECT started_at FROM turns WHERE id = :target)
);

DELETE FROM turns
WHERE started_at >= (SELECT started_at FROM turns WHERE id = :target);

COMMIT;
```

What survives, and why:

| Survives | Why |
| --- | --- |
| The session row | a rollback rewinds a conversation, it does not delete one |
| The root execution | it has `created_by_turn = NULL`; it predates every turn |
| Turns before the target | that is the conversation being restored to |
| The `before_rollback` checkpoint | it has `turn_id = NULL`, so the cascade leaves it alone. It is the undo |
| Blobs | content-addressed, and the abandoned tail still points at them |

`turns_one_open` is what makes this safe to run: a session with an open turn has
a turn to finish, and the command refuses instead of truncating under a running
agent.

### What rewinds for free

Two things need no work at all, and it is worth naming them so nobody adds a
step for them:

- **The loaded skills.** They are a projection of the transcript, so truncating
  the turns truncates the set: a skill loaded in the turn that was dropped stops
  being in effect, and one loaded before it stays. No row to delete, no cache to
  invalidate.
- **The tree's shape.** The executions a dropped turn summoned are removed by
  the cascade, so the tree redraws itself from the surviving rows.

The only state a rollback has to rewind by hand is the one it cannot derive: the
plan, and the files.

### The plan is part of the state

A roadmap is not append-only history; it is the current plan, and after a
rollback it must be the plan as it was. Two options were considered:

- **Delete items created after the checkpoint**, by `created_at`. Cheap, and
  wrong: a task re-opened by a later turn keeps its old `created_at` and
  survives.
- **Snapshot the plan at the checkpoint**, in a blob. Exact, and nearly free:
  content addressing means an unchanged plan is one extra integer.

The second is the design. `checkpoints.roadmap_blob` holds the root execution's
plan as JSON at the instant of the checkpoint, and the rollback replaces the
rows with it. A plan is a dozen rows; the column costs less than the bug.

The cascade alone is not enough, and this was verified rather than assumed:
after truncating at a turn, a roadmap item created *by* that turn survived,
because a plan row belongs to an execution and not to the turn that added it.
Without the snapshot, a rollback would leave a to-do list for work that no
longer happened.

## The files half

This is the part with real machinery, so it is the part with real measurements.

### Why git objects, and not a copy

Snapshots need three properties: cheap to take, cheap to keep, exact to restore.
Copying the workspace fails the first two at any real size. A journal of the
write tools fails the third: `run` can change anything, and a shell command that
rewrites a thousand files is not a tool the journal can see.

Git already solves all three. A snapshot is **a commit object created without
touching the user's branch, their index, or their worktree**:

```python
def snapshot(repo: Path, checkpoint_id: str, index: Path) -> str:
    """Freeze the worktree into a commit reachable from refs/sigil/checkpoints/<id>."""
    if not index.exists():
        try:
            git(repo, 'read-tree', 'HEAD', index=index)
        except CalledProcessError:
            git(repo, 'read-tree', '--empty', index=index)  # a repo with no commit yet
    git(repo, 'add', '-A', '.', index=index)  # honours .gitignore
    tree = git(repo, 'write-tree', index=index)
    commit = git(repo, 'commit-tree', tree, '-p', 'HEAD', '-m', f'sigil checkpoint {checkpoint_id}')
    git(repo, 'update-ref', f'refs/sigil/checkpoints/{checkpoint_id}', commit)
    return commit
```

Three details that make it safe, each one verified:

- **`GIT_INDEX_FILE` points at a scratch index**, so the user's staged changes
  are exactly as they were. Measured: `git status` reports the same tree before
  and after a snapshot, and `.git/index` is not rewritten.
- **The ref is what keeps it alive.** An unreferenced commit is garbage; a
  commit under `refs/sigil/` survives `git gc --prune=now`. Verified.
- **`add -A` respects `.gitignore`**, so `node_modules/` and `.venv/` are not
  hashed, and a restore never deletes them. Verified.

The snapshot is taken **once per turn, at its start**, which is why a cost in
the tens of milliseconds is affordable. It is not on the checkpoint path — that
one runs once per second and must stay at half a millisecond.

### What it costs

Measured with the algorithm above, on trees of increasing size and on this
repository:

| Tree | Cold (first snapshot) | Warm (reused index) |
| --- | --- | --- |
| 50 files | 8.7 ms | 6.3 ms |
| 444 files (this repo) | 26.7 ms | **6.7 ms** |
| 500 files | 15.9 ms | 9.8 ms |
| 1,000 files × 1 KB | 22.1 ms | 15.2 ms |
| 2,000 files × 8 KB (16.6 MB) | 99.9 ms | 93.8 ms |

Cold is the first snapshot of a workspace, when every file must be read. Warm
reuses the scratch index, so unchanged files are stat-cached and not re-read.
The curve is linear in file count, roughly 50 µs per file, and `git` is the one
doing it.

At turn granularity that is invisible next to a model request. It runs in a
worker thread anyway (`asyncio.to_thread`), because 100 ms on a
two-thousand-file tree is not something the event loop should wait for while a
user types.

### How a restore is exact

Naively checking out the snapshot is wrong in both directions: it leaves files
created after the snapshot behind, and it can delete files that this session
never touched. So the restore is a **diff against a snapshot of the present**:

```python
def restore(repo: Path, target: str, current: str) -> list[str]:
    """Make the worktree match `target`, using `current` to know what to remove."""
    for status, path in diff(repo, current, target):  # A added, M modified, D deleted
        if status == 'D':
            (repo / path).unlink(missing_ok=True)  # existed now, not then
        else:
            (repo / path).write_bytes(git_show(repo, target, path))
```

`current` is the `before_rollback` snapshot, taken one step earlier. That makes
the diff exact instead of a guess, and it makes the whole operation undoable.

Verified end to end on a scratch repository: a file deleted since the target is
recreated, a file created since the target is removed, an ignored directory is
untouched, binaries come back byte for byte (a 10 KB file whose `sha256` matched
afterwards), and `git status` is clean afterwards.

### When it is not a git repository

There is no snapshot, `checkpoints.tree_commit` is `NULL`, and the files half is
**unavailable**. The command then restores the conversation and says so in
`RollbackOutcome.files_unavailable: True`.

Refusing the whole rollback would be worse: the conversation half does not need
git, and a user outside a repository still wants to go back. Faking a filesystem
snapshot with copies would be the worst of the three — unbounded disk, silent
cost, and it would look like it worked until it mattered.

Sigil does not create a shadow repository of its own for this. It was considered
and rejected: a separate object store duplicates every byte it snapshots, while
the user's own repository reuses its objects and needs only the changed files.

## Order of operations

Filesystem work is not transactional, so the order is chosen for the safest
failure, not the shortest path:

```mermaid
sequenceDiagram
    autonumber
    participant Cmd as rollback_to
    participant Repo as workspace (git)
    participant DB as sqlite

    Cmd->>DB: refuse if a turn is open
    Cmd->>Repo: snapshot the present  → before_rollback.tree_commit
    Cmd->>DB: export the tail to a blob → before_rollback.abandoned_blob
    Cmd->>Repo: restore the target tree
    Note over Repo: if this fails, nothing was lost:<br/>the conversation is intact
    Cmd->>DB: BEGIN IMMEDIATE; drop turns and executions; restore the plan; COMMIT
    Cmd->>DB: mark before_rollback complete
```

**Files first, conversation last.** Every failure mode leaves the recoverable
half recoverable:

| Failure | State left behind | Recovery |
| --- | --- | --- |
| The snapshot fails | nothing changed | fix, retry |
| The restore fails halfway | some files rewound, conversation intact | restore from `before_rollback.tree_commit`, or retry |
| The transaction fails | files rewound, conversation intact | retry the truncation, or undo the files |
| The process dies after commit | both halves done | nothing to do |

The `before_rollback` checkpoint is written *before* any destructive step and is
what makes "undo the undo" possible: one command, pointed at it, puts the tree
and the tail back.

## What a rollback cannot do

Each of these is a real limit, not a disclaimer:

- **It restores the whole workspace, including what Sigil did not do.** If the
  user edited files by hand after the checkpoint, those edits are reverted. That
  is what "the workspace as it was" means, and it is why the UI must show the
  diff and require a confirmation rather than rolling back on a keystroke.
- **Ignored files are outside it.** `node_modules/`, `.venv/`, build output: not
  snapshotted, not restored. A rollback does not reinstall dependencies.
- **Anything outside the workspace is outside it.** A shell command that writes
  to `/tmp` or to another repository is not reverted.
- **It does not undo external effects.** A commit pushed, a message sent, an API
  called. Files and conversation only.
- **It cannot go back to a message that is not a turn.** No prompt-level
  targets, by design (see [the unit is the turn](#the-unit-is-the-turn)).
- **The tree snapshot has a cost per turn.** On a two-thousand-file tree it is
  ~100 ms; on a repository where every turn rewrites megabytes of binaries, the
  objects grow with it. Retention below is what keeps that bounded.

## Retention

Every turn leaves a commit and a ref. Three mechanisms keep that from growing
without a ceiling:

1. **Objects are deduplicated by git.** A turn that changes three files adds
   three blobs and one commit, not a copy of the tree.
2. **Refs are cheap** — a file with one line each, under
   `refs/sigil/checkpoints/`.
3. **A maintenance pass drops the refs of checkpoints older than the session's
   retention**, and `git gc` reclaims the objects. Dropping a ref does not touch
   any user commit: those refs point at commits *nobody else references*, which
   is exactly why they exist.

The pass runs with the same maintenance job that vacuums the database
(`storage.md`, phase 6), so there is one place that decides what is old.

## What this changes in the other documents

| Document | Change |
| --- | --- |
| [domain](domain.md) | `Checkpoint` and `Rollback` join the glossary; a new invariant: nothing is dropped without a recoverable copy |
| [storage](storage.md) | `checkpoints` table, `agent_executions.created_by_turn`, blob kinds for the plan and the abandoned tail |
| [architecture](architecture.md) | `rollback_to` is a command, and the core takes the snapshot — never the UI |

## Phases

The schema lands with the rest (phase 1), because a column added later means a
schema step over real rows, and the whole point of designing first is to need as
few of those as possible.

1. **The schema knows about checkpoints.** ✅ `checkpoints`, `created_by_turn`
   and the blob kinds are in the database. Nothing writes them yet.
2. **Conversation rollback.** The command, the export of the abandoned tail, the
   plan restored from its snapshot, the refusal while a turn is open. Tests:
   truncating at each turn of a scripted session leaves exactly the expected
   rows.
3. **File snapshots.** The git adapter, measured above, taken at turn start in a
   worker thread. Tests: the scratch-repository cases already verified here
   become regression tests.
4. **The restore**, with the diff against `before_rollback` and its failure
   matrix.
5. **The UI**: the list of rollback points, the file diff before confirming, and
   the outcome reported honestly — including when the files could not be
   restored.
6. **Retention**, with the maintenance pass.

Phases 2 and 3 are independent, which is deliberate: a session outside a git
repository still gets conversation rollback in phase 2.
