"""Persistence of Sigil sessions (conversations).

Each session is a full conversation: it is saved as a JSON file inside
``~/.sigil/sessions/<cwd-hash>/``. The hash of the *current working directory*
associates the sessions with the directory Sigil was launched from without
exposing the full path as a folder name.

Each directory also keeps an ``index.json`` with the last session id,
so that it can be restored automatically on startup.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
)

from src.engine.providers.domain.selection import Selection
from src.engine.sessions.titling import first_user_message, shorten

DEFAULT_SESSIONS_DIR = Path.home() / '.sigil' / 'sessions'
INDEX_FILENAME = 'index.json'
SESSION_SUFFIX = '.json'


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _selection_from(raw: object) -> Selection | None:
    """Rebuilds a stored selection, tolerating the old format (which had none)."""
    if not isinstance(raw, dict):
        return None
    return Selection.from_dict(raw)


def cwd_hash(cwd: str | Path) -> str:
    """Returns a stable, short hash for a working directory."""
    real = os.path.realpath(str(cwd))
    return hashlib.sha256(real.encode('utf-8')).hexdigest()[:16]


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
    """Reads and writes sessions for a specific working directory."""

    def __init__(self, cwd: str | Path | None = None, base_dir: str | Path | None = None) -> None:
        self.cwd = os.path.realpath(str(cwd if cwd is not None else Path.cwd()))
        self.base_dir = Path(base_dir) if base_dir is not None else DEFAULT_SESSIONS_DIR
        self.directory = self.base_dir / cwd_hash(self.cwd)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_path = self.directory / INDEX_FILENAME

    # --- public API ----------------------------------------------------------

    def rebind(self, cwd: str | Path) -> None:
        """Points the store at another workspace's session directory.

        Switching workspace keeps the same store object: the app and the turn
        machinery hold a reference to it, so its identity cannot change.
        """
        target = os.path.realpath(str(cwd))
        if target == self.cwd:
            return
        self.cwd = target
        self.directory = self.base_dir / cwd_hash(target)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_path = self.directory / INDEX_FILENAME

    def directory_for(self, cwd: str | Path) -> Path:
        """Session directory of any workspace, without moving this store."""
        return self.base_dir / cwd_hash(cwd)

    def create_session(self, title: str | None = None) -> Session:
        """Creates an empty session and persists it immediately."""
        session = Session(id=uuid.uuid4().hex, cwd=self.cwd, title=title)
        self.save(session)
        return session

    def save(self, session: Session) -> None:
        """Persists the session and marks it as the last one used."""
        session.touch()
        self._atomic_write(self._session_path(session.id), self._serialize(session))

        index = self._read_index()
        index['cwd'] = self.cwd
        index['last_session_id'] = session.id
        self._atomic_write(self.index_path, index)

    def load(self, session_id: str) -> Session | None:
        """Loads a session by id and marks it as the last one used."""
        path = self._session_path(session_id)
        if not path.is_file():
            return None

        try:
            session = self._deserialize(json.loads(path.read_text(encoding='utf-8')))
        except OSError, ValueError, KeyError:
            return None

        index = self._read_index()
        index['cwd'] = self.cwd
        index['last_session_id'] = session.id
        self._atomic_write(self.index_path, index)
        return session

    def load_last(self) -> Session | None:
        """Loads the last session used, or the most recent one if there is no index."""
        last_id = self._read_index().get('last_session_id')
        if last_id:
            session = self.load(last_id)
            if session is not None:
                return session

        sessions = self.list_sessions()
        return sessions[0] if sessions else None

    def list_sessions(self) -> list[Session]:
        """Lists the sessions of the directory, newest first."""
        sessions: list[Session] = []
        for path in self.directory.glob(f'*{SESSION_SUFFIX}'):
            if path.name == INDEX_FILENAME:
                continue
            try:
                sessions.append(self._deserialize(json.loads(path.read_text(encoding='utf-8'))))
            except OSError, ValueError, KeyError:
                # Corrupt or incomplete files are ignored instead of breaking the app.
                continue

        sessions.sort(key=lambda session: session.updated_at, reverse=True)
        return sessions

    def find(self, token: str) -> list[Session]:
        """Looks for sessions whose id starts with ``token``."""
        token = token.strip()
        if not token:
            return []
        return [session for session in self.list_sessions() if session.id.startswith(token)]

    # --- Serialization -------------------------------------------------------

    def _session_path(self, session_id: str) -> Path:
        return self.directory / f'{session_id}{SESSION_SUFFIX}'

    def _serialize(self, session: Session) -> dict:
        return {
            'id': session.id,
            'cwd': session.cwd,
            'created_at': session.created_at,
            'updated_at': session.updated_at,
            'title': session.title,
            'selection': session.selection.to_dict() if session.selection is not None else None,
            'agent': session.agent,
            'messages': ModelMessagesTypeAdapter.dump_python(session.messages, mode='json'),
        }

    def _deserialize(self, data: dict) -> Session:
        return Session(
            id=data['id'],
            cwd=data.get('cwd', self.cwd),
            messages=ModelMessagesTypeAdapter.validate_python(data.get('messages', [])),
            created_at=data.get('created_at') or _now(),
            updated_at=data.get('updated_at') or _now(),
            title=data.get('title'),
            selection=_selection_from(data.get('selection')),
            agent=str(data.get('agent') or ''),
        )

    # --- Index and atomic write ----------------------------------------------

    def _read_index(self) -> dict:
        try:
            data = json.loads(self.index_path.read_text(encoding='utf-8'))
        except OSError, ValueError:
            data = {}

        if not isinstance(data, dict):
            data = {}
        data.setdefault('cwd', self.cwd)
        data.setdefault('last_session_id', None)
        return data

    def _atomic_write(self, path: Path, data: dict) -> None:
        tmp = path.with_name(f'{path.name}.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, path)
