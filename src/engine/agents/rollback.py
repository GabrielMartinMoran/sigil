"""Going back to a message: the conversation, the plan, and the files.

A rollback point is a **turn of the root execution with `origin = 'user'`**. A prompt
is not one, because steering stays inside the turn; a subagent's turn is not one
either, because its files were changed as part of the parent's turn — to undo a
subagent the user goes back to the turn that summoned it, and the cascade takes it.

The instant restored is **before that turn started**, so the turn itself is dropped:
it is "put me back where I was before I sent it", which is what a user does when they
want to say something different. Both halves describe the same instant or the state
is a lie.

**Files first, conversation last.** Filesystem work is not transactional, and this
order leaves the recoverable half recoverable on every failure: if the restore dies
halfway the conversation is intact, and the `before_rollback` checkpoint is what puts
the tree and the tail back.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.engine.agents.snapshots import NotARepository, WorkspaceSnapshots
from src.engine.sessions.session_store import SessionStore
from src.engine.storage.blobs import ABANDONED, ROADMAP, Blobs
from src.engine.storage.database import Database
from src.engine.storage.executions import root_id_for
from src.engine.storage.roadmap import plan_as_json, restore_plan_from_json

REASON_PROMPT = 'prompt'
REASON_BEFORE_ROLLBACK = 'before_rollback'
REASON_MANUAL = 'manual'


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    import hashlib
    import uuid

    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """One instant a session can be returned to."""

    id: str
    session_id: str
    execution_id: str
    turn_id: str | None
    at: str
    reason: str
    label: str
    tree_commit: str | None


@dataclass(frozen=True, slots=True)
class RollbackOutcome:
    """What a rollback actually did, without optimism."""

    target_turn_id: str
    turns_dropped: int
    executions_dropped: int
    messages_dropped: int
    files_restored: int
    files_deleted: int
    files_unavailable: bool
    undo_checkpoint: str

    @property
    def touched_files(self) -> bool:
        return self.files_restored > 0 or self.files_deleted > 0


@dataclass(frozen=True, slots=True)
class ForkOutcome:
    """What a fork produced."""

    session_id: str
    turns_copied: int
    messages_copied: int
    executions_copied: int


@dataclass(frozen=True, slots=True)
class RollbackError(Exception):
    """A rollback that could not start, with the reason said plainly."""

    reason: str

    def __str__(self) -> str:
        return self.reason


class Checkpoints:
    """The rows that record where a session and its workspace stood."""

    def __init__(self, database: Database, blobs: Blobs) -> None:
        self._db = database
        self._blobs = blobs

    # --- reading -------------------------------------------------------------

    def of_session(self, session_id: str) -> list[Checkpoint]:
        rows = self._db.query(
            'SELECT * FROM checkpoints WHERE session_id = ? ORDER BY at',
            (session_id,),
        )
        return [_checkpoint_from(row) for row in rows]

    def of_turn(self, turn_id: str) -> Checkpoint | None:
        row = self._db.one('SELECT * FROM checkpoints WHERE turn_id = ?', (turn_id,))
        return None if row is None else _checkpoint_from(row)

    def get(self, checkpoint_id: str) -> Checkpoint | None:
        row = self._db.one('SELECT * FROM checkpoints WHERE id = ?', (checkpoint_id,))
        return None if row is None else _checkpoint_from(row)

    # --- writing -------------------------------------------------------------

    def _plan_before(self, execution_id: str, *, conn: sqlite3.Connection | None = None) -> str:
        """The plan the previous checkpoint of this execution recorded, or an empty one."""
        execute = (conn or self._db.connection).execute
        row = execute(
            'SELECT roadmap_blob FROM checkpoints WHERE execution_id = ? AND roadmap_blob IS NOT NULL '
            'ORDER BY at DESC LIMIT 1',
            (execution_id,),
        ).fetchone()
        return '[]' if row is None else self._blobs.data(int(row[0])).decode('utf-8')

    def take(
        self,
        session_id: str,
        execution_id: str,
        *,
        turn_id: str | None,
        reason: str = REASON_PROMPT,
        label: str = '',
        tree_commit: str | None = None,
        carry_plan: bool = False,
        conn: sqlite3.Connection | None = None,
    ) -> Checkpoint:
        """Records an instant, with the plan as it was.

        The plan goes into a blob because a cascade cannot restore it: a task belongs
        to an execution, so an item created by a dropped turn survives the cascade and
        only a snapshot can tell which items the turn added.

        ``carry_plan`` is what makes a turn's checkpoint mean *before that turn*. The
        per-turn checkpoint is written from a save that already contains the turn's
        messages and its plan changes, so snapshotting the plan "now" would restore a
        plan that includes the work being undone. Carrying the previous checkpoint's
        plan forward records the instant the user actually wants: the state before the
        turn ran. An undo checkpoint passes ``False``: it is written live, before
        anything is touched, so the plan it sees is the one to preserve.
        """
        checkpoint_id = _new_id()
        execute = (conn or self._db.connection).execute
        raw_plan = self._plan_before(execution_id, conn=conn) if carry_plan else plan_as_json(self._db, execution_id)
        roadmap_blob = self._blobs.put(raw_plan or '[]', kind=ROADMAP, media_type='application/json', conn=conn)
        execute(
            'INSERT INTO checkpoints '
            '(id, session_id, execution_id, turn_id, at, reason, label, tree_commit, roadmap_blob) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                checkpoint_id,
                session_id,
                execution_id,
                turn_id,
                _now(),
                reason,
                label,
                tree_commit,
                roadmap_blob,
            ),
        )
        return Checkpoint(
            id=checkpoint_id,
            session_id=session_id,
            execution_id=execution_id,
            turn_id=turn_id,
            at=_now(),
            reason=reason,
            label=label,
            tree_commit=tree_commit,
        )


class Rollback:
    """Returns a session to the instant before one of its turns started."""

    def __init__(
        self,
        store: SessionStore,
        *,
        workspace: str | Path | None = None,
        snapshots: WorkspaceSnapshots | None = None,
        on_plan_changed: Callable[[], None] | None = None,
    ) -> None:
        self._store = store
        self._db = store.database
        self._blobs = store.blobs
        self._snapshots = snapshots or WorkspaceSnapshots(workspace or store.cwd)
        self._checkpoints = Checkpoints(self._db, self._blobs)
        self._on_plan_changed = on_plan_changed

    @property
    def snapshots(self) -> WorkspaceSnapshots:
        return self._snapshots

    @property
    def checkpoints(self) -> Checkpoints:
        return self._checkpoints

    # --- the points a user can go back to ------------------------------------

    def points(self, session_id: str) -> list[Checkpoint]:
        """The turns of the root that a user may return to, newest first."""
        rows = self._db.query(
            'SELECT t.id AS turn_id, t.started_at, c.id AS checkpoint_id '
            'FROM turns t '
            'LEFT JOIN checkpoints c ON c.turn_id = t.id '
            "WHERE t.execution_id = ? AND t.origin = 'user' "
            'ORDER BY t.started_at DESC',
            (root_id_for(session_id),),
        )
        found: list[Checkpoint] = []
        for row in rows:
            if row['checkpoint_id'] is not None:
                checkpoint = self._checkpoints.get(str(row['checkpoint_id']))
                if checkpoint is not None:
                    found.append(checkpoint)
                    continue
            found.append(
                Checkpoint(
                    id='',
                    session_id=session_id,
                    execution_id=root_id_for(session_id),
                    turn_id=str(row['turn_id']),
                    at=str(row['started_at']),
                    reason=REASON_PROMPT,
                    label='',
                    tree_commit=None,
                )
            )
        return found

    # --- the command ---------------------------------------------------------

    def to_turn(self, session_id: str, turn_id: str, *, files: bool = True) -> RollbackOutcome:
        """Rolls back to before ``turn_id``. See the module docstring for the order."""
        target = self._db.one(
            'SELECT id, execution_id, origin, started_at FROM turns WHERE id = ? AND execution_id = ?',
            (turn_id, root_id_for(session_id)),
        )
        if target is None:
            raise RollbackError(f'No turn {turn_id!r} in the root execution of this session.')
        if str(target['origin']) != 'user':
            raise RollbackError('Only a turn the user started can be a rollback point.')
        open_turn = self._db.value(
            'SELECT count(*) FROM turns WHERE execution_id = ? AND finished_at IS NULL',
            (root_id_for(session_id),),
        )
        if open_turn:
            raise RollbackError('A turn is still running: finish or stop it before rolling back.')

        checkpoint = self._checkpoints.of_turn(turn_id)
        started_at = str(target['started_at'])

        # 1. The undo, and the tail it preserves. Written before anything destructive.
        undo_commit = self._snapshot()
        undo = self._checkpoints.take(
            session_id,
            root_id_for(session_id),
            turn_id=None,
            reason=REASON_BEFORE_ROLLBACK,
            label=f'before rolling back to {started_at}',
            tree_commit=undo_commit,
        )
        self._export_tail(session_id, started_at, undo.id)

        # 2. The files. If this dies halfway the conversation is still intact.
        restored = deleted = 0
        unavailable = False
        if files:
            restored, deleted, unavailable = self._restore_files(
                target_commit=None if checkpoint is None else checkpoint.tree_commit,
                current_commit=undo_commit,
            )

        # The plan has to be read before the truncation: the target checkpoint hangs
        # off its own turn with ON DELETE CASCADE, so dropping the turn destroys the
        # row that holds the plan to put back.
        plan = None if checkpoint is None else self._plan_of(checkpoint)

        # 3. The conversation, in one transaction: turns, executions, plan.
        turns_dropped, executions_dropped, messages_dropped = self._truncate(session_id, started_at)
        if plan is not None:
            self._write_plan(checkpoint.execution_id, plan)

        return RollbackOutcome(
            target_turn_id=turn_id,
            turns_dropped=turns_dropped,
            executions_dropped=executions_dropped,
            messages_dropped=messages_dropped,
            files_restored=restored,
            files_deleted=deleted,
            files_unavailable=unavailable,
            undo_checkpoint=undo.id,
        )

    def fork_to(self, session_id: str, turn_id: str, *, title: str | None = None) -> ForkOutcome:
        """Branches a new session at the instant before ``turn_id``.

        The same instant a rollback restores, with one difference that decides
        everything else: **nothing is destroyed and the workspace is not touched.** A
        rollback puts the world back; a fork lets you try something else and keeps both
        answers.

        The prefix is copied, not linked: a new session needs its own executions,
        turns and messages, or the first write to either one would move the other.
        """
        target = self._db.one(
            'SELECT id, execution_id, origin, started_at FROM turns WHERE id = ? AND execution_id = ?',
            (turn_id, root_id_for(session_id)),
        )
        if target is None:
            raise RollbackError(f'No turn {turn_id!r} in the root execution of this session.')
        if str(target['origin']) != 'user':
            raise RollbackError('Only a turn the user started can be a fork point.')

        source = self._db.one('SELECT * FROM sessions WHERE id = ?', (session_id,))
        if source is None:
            raise RollbackError(f'No session {session_id!r}.')

        started_at = str(target['started_at'])
        new_id = _new_id()
        new_root = root_id_for(new_id)
        checkpoint = self._checkpoints.of_turn(turn_id)
        plan = None if checkpoint is None else self._plan_of(checkpoint)

        turns_copied = messages_copied = executions_copied = 0
        with self._db.transaction() as conn:
            conn.execute(
                'INSERT INTO sessions (id, workspace_id, title, preview, account_id, model, thinking, '
                'window, efforts, images, created_at, updated_at) '
                'SELECT ?, workspace_id, ?, preview, account_id, model, thinking, window, efforts, '
                'images, created_at, ? FROM sessions WHERE id = ?',
                (new_id, title or _fork_title(source['title']), _now(), session_id),
            )

            # Every execution that existed before the boundary: the root, and the
            # subagents whose summoning turn is part of the prefix.
            executions = self._db.query(
                'SELECT * FROM agent_executions WHERE session_id = ? ORDER BY depth, started_at',
                (session_id,),
            )
            for execution in executions:
                created_by = execution['created_by_turn']
                if created_by is not None and not self._turn_before(str(created_by), started_at):
                    continue
                new_execution = new_root if execution['parent_id'] is None else _new_id()
                parent = None if execution['parent_id'] is None else str(execution['parent_id'])
                mapped_parent = new_root if parent == root_id_for(session_id) else parent
                conn.execute(
                    'INSERT INTO agent_executions '
                    '(id, session_id, parent_id, created_by_turn, name, role, profile, depth, brief, '
                    'status, mode, model, result, error, started_at, finished_at, turns, delivered, '
                    'channel, max_steps, budget_exhausted) '
                    'VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (
                        new_execution,
                        new_id,
                        mapped_parent,
                        execution['name'],
                        execution['role'],
                        execution['profile'],
                        execution['depth'],
                        execution['brief'],
                        execution['status'],
                        execution['mode'],
                        execution['model'],
                        execution['result'],
                        execution['error'],
                        execution['started_at'],
                        execution['finished_at'],
                        execution['turns'],
                        execution['delivered'],
                        execution['channel'],
                        execution['max_steps'],
                        execution['budget_exhausted'],
                    ),
                )
                executions_copied += 1
                turns_copied += self._copy_turns(
                    conn,
                    str(execution['id']),
                    new_execution,
                    before=started_at,
                    messages=messages_copied,
                )
                messages_copied += self._copy_messages(conn, str(execution['id']), new_execution, before=started_at)

            # The plan of the fork is the plan at the boundary, not the current one.
            if plan is not None:
                self._blobs.put(plan, kind=ROADMAP, media_type='application/json', conn=conn)

        if plan is not None:
            self._write_plan(new_root, plan)
        return ForkOutcome(
            session_id=new_id,
            turns_copied=turns_copied,
            messages_copied=messages_copied,
            executions_copied=executions_copied,
        )

    def undo(self, session_id: str, undo_checkpoint: str) -> RollbackOutcome:
        """Puts back what a rollback dropped, using its `before_rollback` checkpoint.

        The tail is restored as a transcript, not as turns: the rows are re-inserted
        with their original ids and sequence numbers, and the plan comes back from the
        blob the checkpoint kept.
        """
        checkpoint = self._checkpoints.get(undo_checkpoint)
        if checkpoint is None or checkpoint.reason != REASON_BEFORE_ROLLBACK:
            raise RollbackError(f'{undo_checkpoint!r} is not an undo checkpoint.')
        restored = deleted = 0
        unavailable = False
        if checkpoint.tree_commit is not None:
            current = self._snapshot()
            restored, deleted, unavailable = self._restore_files(
                target_commit=checkpoint.tree_commit,
                current_commit=current,
            )
        rows = self._db.one('SELECT abandoned_blob FROM checkpoints WHERE id = ?', (undo_checkpoint,))
        if rows is not None and rows['abandoned_blob'] is not None:
            self._import_tail(session_id, int(rows['abandoned_blob']))
        self._restore_plan(checkpoint)
        return RollbackOutcome(
            target_turn_id=checkpoint.turn_id or '',
            turns_dropped=0,
            executions_dropped=0,
            messages_dropped=0,
            files_restored=restored,
            files_deleted=deleted,
            files_unavailable=unavailable,
            undo_checkpoint=undo_checkpoint,
        )

    # --- the halves ----------------------------------------------------------

    def _snapshot(self) -> str | None:
        """A snapshot of the present, or ``None`` outside a repository."""
        try:
            return self._snapshots.take(_new_id())
        except NotARepository, OSError, subprocess.SubprocessError:
            return None

    def _restore_files(
        self,
        *,
        target_commit: str | None,
        current_commit: str | None,
    ) -> tuple[int, int, bool]:
        """The files half. ``files_unavailable`` when there is no snapshot to go to."""
        if target_commit is None or current_commit is None:
            return 0, 0, True
        try:
            touched = self._snapshots.restore(target_commit, current_commit)
        except NotARepository, OSError, subprocess.SubprocessError:
            return 0, 0, True
        restored = sum(1 for entry in touched if entry.startswith(('a ', 'm ')))
        deleted = sum(1 for entry in touched if entry.startswith('deleted '))
        return restored, deleted, False

    def _truncate(self, session_id: str, started_at: str) -> tuple[int, int, int]:
        """Drops the turns from ``started_at`` on, and what only they created."""
        root = root_id_for(session_id)
        with self._db.transaction() as conn:
            # Counted first: deleting the executions cascades into the messages, so a
            # count taken after it would always be zero.
            messages_before = conn.execute(
                'SELECT count(*) FROM messages WHERE turn_id IN '
                '(SELECT id FROM turns WHERE execution_id = ? AND started_at >= ?)',
                (root, started_at),
            ).fetchone()[0]
            executions = conn.execute(
                'DELETE FROM agent_executions WHERE session_id = ? AND created_by_turn IN '
                '(SELECT id FROM turns WHERE execution_id = ? AND started_at >= ?)',
                (session_id, root, started_at),
            ).rowcount
            turns = conn.execute(
                'DELETE FROM turns WHERE execution_id = ? AND started_at >= ?',
                (root, started_at),
            ).rowcount
            # The plan of the root is restored from the checkpoint, not derived.
            conn.execute('DELETE FROM roadmap_items WHERE execution_id = ?', (root,))
        return int(turns or 0), int(executions or 0), int(messages_before or 0)

    def _turn_before(self, turn_id: str, started_at: str) -> bool:
        row = self._db.one('SELECT started_at FROM turns WHERE id = ?', (turn_id,))
        return row is not None and str(row['started_at']) < started_at

    def _copy_turns(
        self,
        conn: sqlite3.Connection,
        source_execution: str,
        destination_execution: str,
        *,
        before: str,
        messages: int,
    ) -> int:
        """Copies the turns of one execution that started before the boundary."""
        rows = self._db.query(
            'SELECT * FROM turns WHERE execution_id = ? AND started_at < ? ORDER BY started_at',
            (source_execution, before),
        )
        for row in rows:
            conn.execute(
                'INSERT INTO turns '
                '(id, execution_id, origin, started_at, finished_at, outcome, steps, cost, library_run_id) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    _forked_id(str(row['id']), destination_execution),
                    destination_execution,
                    row['origin'],
                    row['started_at'],
                    row['finished_at'],
                    row['outcome'],
                    row['steps'],
                    row['cost'],
                    row['library_run_id'],
                ),
            )
        return len(rows)

    def _copy_messages(
        self,
        conn: sqlite3.Connection,
        source_execution: str,
        destination_execution: str,
        *,
        before: str,
    ) -> int:
        """Copies the messages that belong to the turns of the prefix."""
        rows = self._db.query(
            'SELECT m.* FROM messages m JOIN turns t ON t.id = m.turn_id '
            'WHERE m.execution_id = ? AND t.started_at < ? ORDER BY m.seq',
            (source_execution, before),
        )
        for row in rows:
            conn.execute(
                'INSERT OR IGNORE INTO messages '
                '(execution_id, turn_id, seq, kind, payload, instructions_id, input_tokens, '
                'cache_read, cache_write, output_tokens, cost, timestamp) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    destination_execution,
                    _forked_id(str(row['turn_id']), destination_execution),
                    row['seq'],
                    row['kind'],
                    row['payload'],
                    row['instructions_id'],
                    row['input_tokens'],
                    row['cache_read'],
                    row['cache_write'],
                    row['output_tokens'],
                    row['cost'],
                    row['timestamp'],
                ),
            )
        return len(rows)

    def _plan_of(self, checkpoint: Checkpoint) -> str | None:
        """The plan a checkpoint recorded, read while its row still exists."""
        row = self._db.one('SELECT roadmap_blob FROM checkpoints WHERE id = ?', (checkpoint.id,))
        if row is None or row['roadmap_blob'] is None:
            return None
        return self._blobs.data(int(row['roadmap_blob'])).decode('utf-8')

    def _write_plan(self, execution_id: str, raw: str) -> int:
        """Puts a plan back, and tells whoever paints it."""
        restored = restore_plan_from_json(self._db, execution_id, raw)
        if self._on_plan_changed is not None:
            self._on_plan_changed()
        return restored

    def _restore_plan(self, checkpoint: Checkpoint) -> int:
        """Used by the undo path, where the checkpoint is not the one being dropped."""
        raw = self._plan_of(checkpoint)
        return 0 if raw is None else self._write_plan(checkpoint.execution_id, raw)

    def _export_tail(self, session_id: str, started_at: str, undo_id: str) -> int:
        """Exports the turns about to be dropped, so the undo has something to put back."""
        root = root_id_for(session_id)
        turns = [
            str(row['id'])
            for row in self._db.query(
                'SELECT id FROM turns WHERE execution_id = ? AND started_at >= ? ORDER BY started_at',
                (root, started_at),
            )
        ]
        payload: dict[str, object] = {'turns': [], 'messages': [], 'plan': []}
        if turns:
            placeholders = ','.join('?' for _ in turns)
            payload['turns'] = [
                {
                    'id': str(row['id']),
                    'execution_id': str(row['execution_id']),
                    'origin': str(row['origin']),
                    'started_at': str(row['started_at']),
                    'finished_at': None if row['finished_at'] is None else str(row['finished_at']),
                    'outcome': None if row['outcome'] is None else str(row['outcome']),
                    'steps': int(row['steps']),
                    'cost': None if row['cost'] is None else float(row['cost']),
                    'library_run_id': None if row['library_run_id'] is None else str(row['library_run_id']),
                }
                for row in self._db.query(f'SELECT * FROM turns WHERE id IN ({placeholders})', tuple(turns))
            ]
            payload['messages'] = [
                {
                    'execution_id': str(row['execution_id']),
                    'turn_id': str(row['turn_id']),
                    'seq': int(row['seq']),
                    'kind': str(row['kind']),
                    'payload': str(row['payload']),
                    'instructions_id': None if row['instructions_id'] is None else int(row['instructions_id']),
                    'input_tokens': row['input_tokens'],
                    'cache_read': row['cache_read'],
                    'cache_write': row['cache_write'],
                    'output_tokens': row['output_tokens'],
                    'cost': row['cost'],
                    'timestamp': row['timestamp'],
                }
                for row in self._db.query(f'SELECT * FROM messages WHERE turn_id IN ({placeholders})', tuple(turns))
            ]
        payload['plan'] = json.loads(plan_as_json(self._db, root))
        blob = self._blobs.put(
            json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
            kind=ABANDONED,
            media_type='application/json',
        )
        self._db.execute('UPDATE checkpoints SET abandoned_blob = ? WHERE id = ?', (blob, undo_id))
        return blob

    def _import_tail(self, session_id: str, blob_id: int) -> None:
        """Puts back the rows a rollback exported."""
        try:
            payload = json.loads(self._blobs.data(blob_id).decode('utf-8'))
        except ValueError:
            return
        with self._db.transaction() as conn:
            for turn in payload.get('turns', []):
                conn.execute(
                    'INSERT OR IGNORE INTO turns '
                    '(id, execution_id, origin, started_at, finished_at, outcome, steps, cost, library_run_id) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (
                        turn['id'],
                        turn['execution_id'],
                        turn['origin'],
                        turn['started_at'],
                        turn['finished_at'],
                        turn['outcome'],
                        turn['steps'],
                        turn['cost'],
                        turn['library_run_id'],
                    ),
                )
            for message in payload.get('messages', []):
                conn.execute(
                    'INSERT OR IGNORE INTO messages '
                    '(execution_id, turn_id, seq, kind, payload, instructions_id, input_tokens, '
                    'cache_read, cache_write, output_tokens, cost, timestamp) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (
                        message['execution_id'],
                        message['turn_id'],
                        message['seq'],
                        message['kind'],
                        message['payload'],
                        message['instructions_id'],
                        message['input_tokens'],
                        message['cache_read'],
                        message['cache_write'],
                        message['output_tokens'],
                        message['cost'],
                        message['timestamp'],
                    ),
                )


def _forked_id(turn_id: str, execution_id: str) -> str:
    """A turn id for the copy. Same turn, different execution, so no clash."""
    import hashlib

    return hashlib.sha256(f'fork:{execution_id}:{turn_id}'.encode()).hexdigest()[:32]


def _fork_title(title: object) -> str:
    return f'{title} (fork)' if isinstance(title, str) and title else 'fork'


def _checkpoint_from(row) -> Checkpoint:
    return Checkpoint(
        id=str(row['id']),
        session_id=str(row['session_id']),
        execution_id=str(row['execution_id']),
        turn_id=None if row['turn_id'] is None else str(row['turn_id']),
        at=str(row['at']),
        reason=str(row['reason']),
        label=str(row['label'] or ''),
        tree_commit=None if row['tree_commit'] is None else str(row['tree_commit']),
    )


__all__ = [
    'REASON_BEFORE_ROLLBACK',
    'REASON_MANUAL',
    'REASON_PROMPT',
    'Checkpoint',
    'Checkpoints',
    'ForkOutcome',
    'Rollback',
    'RollbackError',
    'RollbackOutcome',
]
