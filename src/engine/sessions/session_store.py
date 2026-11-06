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
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai.messages import ModelMessage

from src.engine.agents.tree.tree import AgentTree
from src.engine.providers.domain.selection import Selection
from src.engine.sessions.titling import first_user_message, shorten
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


@dataclass
class Session:
    """A conversation with its pydantic-ai message history."""

    id: str
    cwd: str
    messages: list[ModelMessage] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
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

    def create_session(self, title: str | None = None) -> Session:
        """Creates an empty session and persists it immediately."""
        session = Session(id=uuid.uuid4().hex, cwd=self.cwd, title=title)
        self.save(session)
        return session

    def save(self, session: Session) -> None:
        """Persists the session: the row, its executions and what is new of the history.

        One transaction, so a crash leaves the previous checkpoint intact instead of
        a half-written one. The session's size stopped mattering: an append is
        rows, not a rewrite.
        """
        session.touch()
        selection = session.selection
        with self.database.transaction() as conn:
            conn.execute(
                'INSERT INTO sessions (id, workspace_id, title, preview, account_id, model, thinking, '
                'window, efforts, images, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(id) DO UPDATE SET '
                'title = excluded.title, preview = excluded.preview, account_id = excluded.account_id, '
                'model = excluded.model, thinking = excluded.thinking, window = excluded.window, '
                'efforts = excluded.efforts, images = excluded.images, updated_at = excluded.updated_at',
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
                    session.created_at,
                    session.updated_at,
                ),
            )
            conn.execute(
                'UPDATE workspaces SET last_session_id = ?, updated_at = ? WHERE id = ?',
                (session.id, session.updated_at, self.workspace_id),
            )
            save_executions(
                self.database,
                self.blobs,
                session.id,
                session.tree,
                session.messages,
                conn=conn,
                root_name=session.agent,
            )

    def load(self, session_id: str) -> Session | None:
        """Loads a session by id and marks it as the last one used."""
        row = self.database.one('SELECT * FROM sessions WHERE id = ?', (session_id,))
        if row is None:
            return None
        session = self._session_from(row)
        with self.database.transaction() as conn:
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
            title=None if row['title'] is None else str(row['title']),
            selection=_selection_from(row),
            agent='',
        )
        session.messages = root_messages(self.database, self.blobs, session.id)
        session.tree = load_tree(self.database, self.blobs, session.id)
        root = session.tree.root if session.tree is not None else None
        session.agent = '' if root is None else root.name
        return session


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
