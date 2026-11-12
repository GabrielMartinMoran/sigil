"""Persistence of Sigil sessions, backed by the database.

A session is one conversation and everything needed to resume it: the transcript of
its root execution, the tree of executions it summoned, the profile and the model it
ran on, its title. It lives in `$SIGIL_HOME/sigil.db`, one database for every
workspace: a session belongs to a directory, and `/load` lists the sessions of every
worktree of the repository, which is a join when the rows live together.

`SessionStore` keeps the small delta API: `save()` writes what is new instead of
rewriting the conversation, which is what used to make a turn cost more and more as
it went on (332 ms per checkpoint at 1,400 messages, against 0.5 ms now).
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

from src.engine.agents.tree.tree import AgentTree
from src.engine.providers.domain.selection import Selection
from src.engine.sessions.titling import first_user_message, shorten
from src.engine.storage.accepted_inputs import (
    AcceptedInput,
    DeliveryConfirmation,
    DeliveryConflictError,
    ImageInput,
    InputKind,
    accept,
    confirm_delivery,
)
from src.engine.storage.blobs import Blobs
from src.engine.storage.database import Database, database_path
from src.engine.storage.executions import (
    load_tree,
    root_id_for,
    root_messages,
    save_executions,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def cwd_hash(cwd: str | Path) -> str:
    """Kept for callers that still hash a directory. The database keys by path."""
    import hashlib

    real = os.path.realpath(str(cwd))
    return hashlib.sha256(real.encode('utf-8')).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """What the picker needs, without the transcript.

    Asking for this used to mean parsing every session file of every workspace —
    396 MB of JSON to paint a list of titles. It is one query now, and it is the
    interface change that matters most outside storage.
    """

    id: str
    workspace: str
    title: str | None
    preview: str
    updated_at: str
    profile: str

    @property
    def short_id(self) -> str:
        return self.id[:8]

    @property
    def display_title(self) -> str:
        """The generated title, or the first message cut short."""
        if self.title:
            return self.title
        return shorten(self.preview)


class SessionConflictError(RuntimeError):
    """Another process changed this session; reload it before saving."""


@dataclass(frozen=True, slots=True)
class SaveResult:
    """Which session row a checkpoint wrote.

    ``session_id`` is the logical session; when a recovery copy was written,
    ``recovery_session_id`` names it and ``destination_id`` is the session whose
    transcript received any confirmed deliveries.
    """

    session_id: str
    recovery_session_id: str | None = None

    @property
    def destination_id(self) -> str:
        return self.recovery_session_id or self.session_id


@dataclass
class Session:
    """A conversation with its pydantic-ai message history."""

    id: str
    cwd: str
    messages: list[ModelMessage] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    _persisted_at: str | None = field(default=None, repr=False, compare=False)
    _draft: bool = field(default=False, repr=False, compare=False)
    _baseline: str | None = field(default=None, repr=False, compare=False)
    _recovery_session: Session | None = field(default=None, repr=False, compare=False)
    title: str | None = None
    selection: Selection | None = None
    """Provider account, model and reasoning level the conversation runs on.

    It travels with the session on purpose: resuming a conversation on a
    different model would silently change cost and behaviour.
    """

    agent: str = ''
    """Agent profile the conversation runs with, by name.

    It travels with the session for the same reason as the model: the
    instructions a conversation was built under are part of it. An empty string
    means "no choice recorded", and the registry falls back to its default.
    """

    tree: AgentTree | None = None
    """The agents of the session: the root the user talks to, and its subagents.

    It is not derived from `agent`: the tree keeps the runs that happened, and a
    subagent that finished an hour ago is part of the conversation's history even
    though nothing is running now.
    """

    allow_external_files: bool = False
    mcp_overrides: dict[str, bool] = field(default_factory=dict)

    tools_tokens: int = 0
    """What the last request measured of the tool schemas.

    Measured per request and written here so `/load` gets the budget back. `0` means
    "not measured in this session yet", which is the truth for a session written
    before these columns existed: the first request of the resumed conversation fills
    them in.
    """

    instructions_tokens: int = 0
    """What the last request measured of the instruction blocks. See `tools_tokens`."""

    @property
    def root_id(self) -> str:
        """The execution that holds this conversation's transcript.

        Derived from the id, so the store and the tree agree on it before either
        has written a row.
        """
        return root_id_for(self.id)

    def touch(self) -> None:
        self.updated_at = _now()

    @property
    def short_id(self) -> str:
        return self.id[:8]

    @property
    def preview(self) -> str:
        """First user message, useful to list sessions."""
        return first_user_message(self.messages)

    @property
    def display_title(self) -> str:
        """What the session is called in the window and in the listing.

        The generated title when there is one. A session that has not sent a
        message yet, or whose title request failed, falls back to the first
        message cut to the same length: an unnamed session must still be
        recognizable in `/load` instead of reading as a bare id.
        """
        if self.title:
            return self.title
        return shorten(self.preview)


class SessionStore:
    """Reads and writes sessions, for a working directory and its workspace row."""

    def __init__(self, cwd: str | Path | None = None, base_dir: str | Path | None = None) -> None:
        self.cwd = os.path.realpath(str(cwd if cwd is not None else Path.cwd()))
        self.base_dir = Path(base_dir) if base_dir is not None else database_path().parent
        self.database = Database.at(database_path(self.base_dir))
        self.blobs = Blobs(self.database)
        self.workspace_id = self._workspace_id()

    # --- workspace -----------------------------------------------------------

    def _workspace_id(self) -> int:
        with self.database.transaction() as conn:
            conn.execute(
                'INSERT OR IGNORE INTO workspaces (path, updated_at) VALUES (?, ?)',
                (self.cwd, _now()),
            )
            row = conn.execute('SELECT id FROM workspaces WHERE path = ?', (self.cwd,)).fetchone()
        return int(row[0])

    def rebind(self, cwd: str | Path) -> None:
        """Points the store at another workspace.

        Switching workspace keeps the same store object: the app and the turn
        machinery hold a reference to it, so its identity cannot change.
        """
        target = os.path.realpath(str(cwd))
        if target == self.cwd:
            return
        self.cwd = target
        self.workspace_id = self._workspace_id()

    # --- public API ----------------------------------------------------------

    def draft_session(self) -> Session:
        """Create an in-memory conversation until its first user message."""
        return Session(id=uuid.uuid4().hex, cwd=self.cwd, _draft=True)

    def create_session(self, title: str | None = None) -> Session:
        """Creates an empty session and persists it immediately."""
        session = Session(id=uuid.uuid4().hex, cwd=self.cwd, title=title)
        self.save(session)
        return session

    def save(
        self,
        session: Session,
        *,
        _inside_transaction: Callable[[sqlite3.Connection], None] | None = None,
        confirmations: Sequence[DeliveryConfirmation] = (),
        recovery_of: str | None = None,
    ) -> SaveResult | None:
        """Persists the session: the row, its executions and what is new of the history.

        One transaction, so a crash leaves the previous checkpoint intact instead of
        a half-written one. The session's size stopped mattering: an append is
        rows, not a rewrite.

        ``confirmations`` are applied in that same transaction, after the transcript
        and before the optional callback: a checkpoint cannot record a delivery
        without the messages it carried. ``recovery_of`` writes the
        original-to-recovery link in the same unit. Returns which session was
        written, or ``None`` when an empty draft is skipped.
        """
        if confirmations and not session.messages:
            raise ValueError('Delivery cannot be confirmed without a transcript')
        if (
            _inside_transaction is None
            and session._draft
            and not any(
                isinstance(part, UserPromptPart)
                for message in session.messages
                if isinstance(message, ModelRequest)
                for part in message.parts
            )
        ):
            return None
        selection = session.selection
        with self.database.transaction() as conn:
            row = conn.execute('SELECT updated_at FROM sessions WHERE id = ?', (session.id,)).fetchone()
            if row is not None and session._persisted_at is not None and row[0] != session._persisted_at:
                raise SessionConflictError('Session changed in another process; reload it before saving.')
            session.touch()
            conn.execute(
                'INSERT INTO sessions (id, workspace_id, title, preview, account_id, model, thinking, '
                'window, efforts, images, tools_tokens, instructions_tokens, allow_external_files, mcp_overrides, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(id) DO UPDATE SET '
                'title = excluded.title, preview = excluded.preview, account_id = excluded.account_id, '
                'model = excluded.model, thinking = excluded.thinking, window = excluded.window, '
                'efforts = excluded.efforts, images = excluded.images, '
                'tools_tokens = excluded.tools_tokens, '
                'instructions_tokens = excluded.instructions_tokens, allow_external_files = excluded.allow_external_files, '
                'mcp_overrides = excluded.mcp_overrides, updated_at = excluded.updated_at',
                (
                    session.id,
                    self.workspace_id,
                    session.title,
                    session.preview,
                    None if selection is None else selection.account_id,
                    None if selection is None else selection.model,
                    None if selection is None else str(selection.thinking),
                    None if selection is None else selection.window,
                    json.dumps([] if selection is None else list(selection.efforts)),
                    1 if selection is None or selection.images else 0,
                    int(session.tools_tokens),
                    int(session.instructions_tokens),
                    int(session.allow_external_files),
                    json.dumps(session.mcp_overrides),
                    session.created_at,
                    session.updated_at,
                ),
            )
            conn.execute(
                'UPDATE workspaces SET last_session_id = ?, updated_at = ? WHERE id = ?',
                (session.id, session.updated_at, self.workspace_id),
            )
            root_id = save_executions(
                self.database,
                self.blobs,
                session.id,
                session.tree,
                session.messages,
                conn=conn,
                root_name=session.agent,
            )
            # Every user turn becomes a rollback point, in the same transaction that
            # creates it. It is two inserts: the row and the plan as it was at that
            # instant. The tree snapshot is not taken here — it costs milliseconds and
            # this runs once per second — and lands later, in a worker thread.
            from src.engine.agents.rollback import Checkpoints

            checkpoints = Checkpoints(self.database, self.blobs)
            for row in conn.execute(
                'SELECT t.id, t.started_at FROM turns t '
                'LEFT JOIN checkpoints c ON c.turn_id = t.id '
                "WHERE t.execution_id = ? AND t.origin = 'user' AND c.id IS NULL "
                'ORDER BY t.started_at',
                (root_id,),
            ).fetchall():
                checkpoints.take(
                    session.id,
                    root_id,
                    turn_id=str(row['id']),
                    carry_plan=False,
                    conn=conn,
                )
            if recovery_of is not None:
                conn.execute(
                    'INSERT INTO recovery_links (original_session_id, destination_id, destination_session_id) '
                    'VALUES (?, ?, ?) '
                    'ON CONFLICT(original_session_id, destination_id) DO UPDATE SET '
                    'destination_session_id = excluded.destination_session_id',
                    (recovery_of, session.id, session.id),
                )
            if confirmations:
                confirm_delivery(conn, confirmations, destination_id=session.id)
            if _inside_transaction is not None:
                _inside_transaction(conn)

        session._persisted_at = session.updated_at
        session._draft = False
        session._baseline = self._fingerprint(session)
        return SaveResult(
            session_id=session.id if recovery_of is None else recovery_of,
            recovery_session_id=session.id if recovery_of is not None else None,
        )

    def accept_input(
        self, session: Session, request_id: str, text: str, images: list[ImageInput], kind: InputKind
    ) -> AcceptedInput:
        """Accept one input, materializing a draft in the same transaction."""
        if not session._draft:
            with self.database.transaction() as conn:
                return accept(conn, session.id, request_id, text, images, kind)
        pending: list[AcceptedInput] = []
        candidate = replace(session)

        def enqueue(conn: sqlite3.Connection) -> None:
            pending.append(accept(conn, session.id, request_id, text, images, kind))

        self.save(candidate, _inside_transaction=enqueue)
        session.updated_at = candidate.updated_at
        session._persisted_at = candidate._persisted_at
        session._draft = candidate._draft
        session._baseline = candidate._baseline
        return pending[0]

    @staticmethod
    def _fingerprint(session: Session) -> str:
        from src.engine.sessions.synchronization import fingerprint

        return fingerprint(
            session.messages,
            session.tree,
            {
                'title': session.title,
                'selection': None if session.selection is None else session.selection.to_dict(),
                'agent': session.agent,
                'allow_external_files': session.allow_external_files,
                'mcp_overrides': session.mcp_overrides,
            },
        )

    @staticmethod
    def _recovery_copy(session: Session) -> Session:
        from src.engine.sessions.synchronization import copy_tree

        recovery = session._recovery_session or replace(
            session, id=uuid.uuid4().hex, _persisted_at=None, _baseline=None, _recovery_session=None
        )
        recovery.messages = list(session.messages)
        recovery.selection = session.selection
        recovery.agent = session.agent
        recovery.allow_external_files = session.allow_external_files
        recovery.mcp_overrides = dict(session.mcp_overrides)
        recovery.title = f'{session.title or session.short_id} (recovered local changes)'
        recovery.tree = copy_tree(session.tree, recovery.id)
        return recovery

    def _preserve_local(
        self, session: Session, *, confirmations: Sequence[DeliveryConfirmation] = ()
    ) -> SaveResult | None:
        if session._baseline == self._fingerprint(session):
            if confirmations:
                raise DeliveryConflictError('Delivery cannot be confirmed without a progress save')
            return None
        recovery = self._recovery_copy(session)
        try:
            result = self.save(recovery, confirmations=confirmations, recovery_of=session.id)
        except SessionConflictError:
            # A recovery session can itself be opened elsewhere. Keep that edit
            # and checkpoint this writer into a fresh copy instead of retrying it.
            session._recovery_session = None
            recovery = self._recovery_copy(session)
            result = self.save(recovery, confirmations=confirmations, recovery_of=session.id)
        session._recovery_session = recovery
        self.database.execute('UPDATE workspaces SET last_session_id = ? WHERE id = ?', (session.id, self.workspace_id))
        return result

    def save_progress_result(
        self, session: Session, *, confirmations: Sequence[DeliveryConfirmation] = ()
    ) -> SaveResult | None:
        """Checkpoint progress and report which session row was written.

        ``recovery_session_id`` names the copy when a conflicting writer forced one;
        the link and any delivery facts were written in that copy's transaction.
        """
        try:
            return self.save(session, confirmations=confirmations)
        except SessionConflictError:
            return self._preserve_local(session, confirmations=confirmations)

    def save_progress(self, session: Session, *, confirmations: Sequence[DeliveryConfirmation] = ()) -> str | None:
        """Checkpoint without replacing newer disk state or interrupting a live run."""
        result = self.save_progress_result(session, confirmations=confirmations)
        return None if result is None else result.recovery_session_id

    def refresh(self, session: Session) -> Session | None:
        """Return a newer consistent snapshot, preserving any divergent local work."""
        with self.database.transaction() as conn:
            row = conn.execute('SELECT * FROM sessions WHERE id = ?', (session.id,)).fetchone()
            if row is None or row['updated_at'] == session._persisted_at:
                return None
            latest = self._session_from(row)
        self._preserve_local(session)
        return latest

    def save_current(self, session: Session) -> Session:
        """Save an idle session, or reload the winner instead of crashing the client."""
        try:
            self.save(session)
        except SessionConflictError:
            return self.refresh(session) or session
        return session

    def load(self, session_id: str) -> Session | None:
        """Loads a session by id and marks it as the last one used."""
        with self.database.transaction() as conn:
            row = conn.execute('SELECT * FROM sessions WHERE id = ?', (session_id,)).fetchone()
            if row is None:
                return None
            session = self._session_from(row)
            conn.execute(
                'UPDATE workspaces SET last_session_id = ?, updated_at = ? WHERE id = ?',
                (session.id, _now(), self.workspace_id),
            )
        return session

    def load_last(self) -> Session | None:
        """Loads the last session used, or the most recent one if there is no index."""
        last_id = self.database.value('SELECT last_session_id FROM workspaces WHERE id = ?', (self.workspace_id,))
        if last_id:
            session = self.load(str(last_id))
            if session is not None:
                return session
        summaries = self.list_sessions()
        return self.load(summaries[0].id) if summaries else None

    def list_sessions(self) -> list[SessionSummary]:
        """The sessions of this workspace, newest first, without their transcripts."""
        rows = self.database.query(
            'SELECT s.id, s.title, s.preview, s.updated_at, w.path AS workspace, '
            "COALESCE(e.profile, '') AS profile "
            'FROM sessions s '
            'JOIN workspaces w ON w.id = s.workspace_id '
            'LEFT JOIN agent_executions e ON e.session_id = s.id AND e.parent_id IS NULL '
            'WHERE s.workspace_id = ? '
            'ORDER BY s.updated_at DESC',
            (self.workspace_id,),
        )
        return [_summary_from(row) for row in rows]

    def find(self, token: str) -> list[SessionSummary]:
        """Looks for sessions whose id starts with ``token``."""
        token = token.strip()
        if not token:
            return []
        return [summary for summary in self.list_sessions() if summary.id.startswith(token)]

    def delete(self, session_id: str) -> bool:
        """Removes a session and everything that hangs from it, by cascade."""
        with self.database.transaction() as conn:
            cursor = conn.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
        return bool(cursor.rowcount)

    # --- rebuilding ----------------------------------------------------------

    def _session_from(self, row) -> Session:
        session = Session(
            id=str(row['id']),
            cwd=self.cwd,
            created_at=str(row['created_at']),
            updated_at=str(row['updated_at']),
            _persisted_at=str(row['updated_at']),
            title=None if row['title'] is None else str(row['title']),
            selection=_selection_from(row),
            agent='',
            tools_tokens=int(row['tools_tokens'] or 0),
            instructions_tokens=int(row['instructions_tokens'] or 0),
            allow_external_files=bool(row['allow_external_files']),
            mcp_overrides=_mcp_overrides_from(row['mcp_overrides']),
        )
        session.messages = root_messages(self.database, self.blobs, session.id)
        session.tree = load_tree(self.database, self.blobs, session.id)
        root = session.tree.root if session.tree is not None else None
        session.agent = '' if root is None else root.name
        session._baseline = self._fingerprint(session)
        return session


def _mcp_overrides_from(raw: str) -> dict[str, bool]:
    try:
        values = json.loads(raw)
    except TypeError, ValueError:
        return {}
    if not isinstance(values, dict):
        return {}
    return {name: enabled for name, enabled in values.items() if isinstance(name, str) and isinstance(enabled, bool)}


def _summary_from(row) -> SessionSummary:
    return SessionSummary(
        id=str(row['id']),
        workspace=str(row['workspace']),
        title=None if row['title'] is None else str(row['title']),
        preview=str(row['preview'] or ''),
        updated_at=str(row['updated_at']),
        profile=str(row['profile'] or ''),
    )


def _selection_from(row) -> Selection | None:
    """Rebuilds the model choice from the flattened columns."""
    account_id = row['account_id']
    model = row['model']
    if not account_id or not model:
        return None
    raw_efforts = row['efforts']
    try:
        efforts = json.loads(raw_efforts) if raw_efforts else []
    except ValueError:
        efforts = []
    return Selection.from_dict(
        {
            'account_id': str(account_id),
            'model': str(model),
            'thinking': row['thinking'],
            'window': row['window'],
            'efforts': efforts if isinstance(efforts, list) else [],
            'images': bool(row['images']),
        }
    )


__all__ = ['Session', 'SessionStore', 'SessionSummary', 'cwd_hash']
