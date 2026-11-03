"""Persistencia de sesiones (conversaciones) de Sigil.

Cada sesión es una conversación completa: se guarda como un archivo JSON dentro
de ``~/.sigil/sessions/<hash-del-cwd>/``. El hash del *current working directory*
permite asociar las sesiones al directorio desde el que se lanzó Sigil sin
exponer la ruta completa como nombre de carpeta.

Cada directorio mantiene además un ``index.json`` con el id de la última sesión,
de modo que al arrancar se pueda restaurar automáticamente.
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
    ModelRequest,
    UserPromptPart,
)

DEFAULT_SESSIONS_DIR = Path.home() / '.sigil' / 'sessions'
INDEX_FILENAME = 'index.json'
SESSION_SUFFIX = '.json'


def _now() -> str:
    return datetime.now(UTC).isoformat()


def cwd_hash(cwd: str | Path) -> str:
    """Devuelve un hash estable y corto para un directorio de trabajo."""
    real = os.path.realpath(str(cwd))
    return hashlib.sha256(real.encode('utf-8')).hexdigest()[:16]


@dataclass
class Session:
    """Una conversación con su historial de mensajes de pydantic-ai."""

    id: str
    cwd: str
    messages: list[ModelMessage] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    title: str | None = None

    def touch(self) -> None:
        self.updated_at = _now()

    @property
    def short_id(self) -> str:
        return self.id[:8]

    @property
    def preview(self) -> str:
        """Primer mensaje del usuario, útil para listar sesiones."""
        for message in self.messages:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    text = ' '.join(part.content.split())
                    if text:
                        return text
        return ''


class SessionStore:
    """Lee y escribe sesiones para un directorio de trabajo concreto."""

    def __init__(self, cwd: str | Path | None = None, base_dir: str | Path | None = None) -> None:
        self.cwd = os.path.realpath(str(cwd if cwd is not None else Path.cwd()))
        self.base_dir = Path(base_dir) if base_dir is not None else DEFAULT_SESSIONS_DIR
        self.directory = self.base_dir / cwd_hash(self.cwd)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_path = self.directory / INDEX_FILENAME

    # --- API pública ---------------------------------------------------------

    def create_session(self, title: str | None = None) -> Session:
        """Crea una sesión vacía y la persiste inmediatamente."""
        session = Session(id=uuid.uuid4().hex, cwd=self.cwd, title=title)
        self.save(session)
        return session

    def save(self, session: Session) -> None:
        """Persiste la sesión y la marca como la última usada."""
        session.touch()
        self._atomic_write(self._session_path(session.id), self._serialize(session))

        index = self._read_index()
        index['cwd'] = self.cwd
        index['last_session_id'] = session.id
        self._atomic_write(self.index_path, index)

    def load(self, session_id: str) -> Session | None:
        """Carga una sesión por id y la marca como la última usada."""
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
        """Carga la última sesión usada, o la más reciente si no hay índice."""
        last_id = self._read_index().get('last_session_id')
        if last_id:
            session = self.load(last_id)
            if session is not None:
                return session

        sessions = self.list_sessions()
        return sessions[0] if sessions else None

    def list_sessions(self) -> list[Session]:
        """Lista las sesiones del directorio, de la más reciente a la más vieja."""
        sessions: list[Session] = []
        for path in self.directory.glob(f'*{SESSION_SUFFIX}'):
            if path.name == INDEX_FILENAME:
                continue
            try:
                sessions.append(self._deserialize(json.loads(path.read_text(encoding='utf-8'))))
            except OSError, ValueError, KeyError:
                # Archivos corruptos o incompletos se ignoran en lugar de romper la app.
                continue

        sessions.sort(key=lambda session: session.updated_at, reverse=True)
        return sessions

    def find(self, token: str) -> list[Session]:
        """Busca sesiones cuyo id empiece por ``token``."""
        token = token.strip()
        if not token:
            return []
        return [session for session in self.list_sessions() if session.id.startswith(token)]

    # --- Serialización -------------------------------------------------------

    def _session_path(self, session_id: str) -> Path:
        return self.directory / f'{session_id}{SESSION_SUFFIX}'

    def _serialize(self, session: Session) -> dict:
        return {
            'id': session.id,
            'cwd': session.cwd,
            'created_at': session.created_at,
            'updated_at': session.updated_at,
            'title': session.title,
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
        )

    # --- Índice y escritura atómica -----------------------------------------

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
