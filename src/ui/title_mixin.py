"""The session title: naming a conversation and putting it in the window title.

Two things share this file because they share one piece of state, the title of the
active session:

- **Naming.** The first message of a session is sent, once, to the model the
  conversation already runs on, and its answer becomes the title
  (``src.engine.sessions.titling``). It happens on a worker while the turn runs,
  so the answer the user is waiting for never waits on a name for the tab.
- **Painting.** The terminal window shows ``<repository> — <session title>``
  (``src.ui.window_title``), repainted on the events that change either half:
  mount, a new or loaded session, a workspace switch and the title arriving.

A failure of either half is silent by design. A provider without the model, an
offline start or a terminal that ignores the sequence leave the session working
and unnamed, and the fallback of ``Session.display_title`` keeps the listing
readable.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from textual import work
from textual.worker import Worker

from src.engine.sessions.titling import first_user_message, generate_title
from src.ui import window_title

MAX_NAMING_ATTEMPTS = 2
"""Requests spent naming one session in this process.

One retry, and only one: a provider that refuses the request now may answer on the
next turn, but a session that cannot be named must not cost a request per message
for the rest of the conversation.
"""


class TitleMixin:
    """Names the session and keeps the terminal window title in sync.

    It expects the app to define ``_session``, ``_store``, ``_agent`` and
    ``_workspace_manager``.
    """

    _session: Any
    _store: Any
    _agent: Any
    _workspace_manager: Any
    _naming_attempts: dict[str, int]
    _naming_workers: dict[str, Worker]

    # --- the title of the session -------------------------------------------

    def repository_label(self) -> str:
        """The repository the session is working in, as the window title names it.

        The repository name, not the folder: that is what identifies the project.
        A linked worktree adds its own label, because two worktrees of the same
        repository are two different trees and a window title that cannot tell
        them apart is worse than no title. Outside a repository there is nothing
        but the folder, which is what is left to show.
        """
        manager = getattr(self, '_workspace_manager', None)
        if manager is None:
            return ''
        repository = manager.repository
        if repository is None:
            return manager.root.name
        workspace = manager.current()
        if workspace.is_worktree:
            return f'{repository.name}:{workspace.label}'
        return repository.name

    def window_title_text(self) -> str:
        """What the terminal window should say right now.

        A subagent on screen is part of the answer: the window is the one place
        that says *which* conversation is open when the terminal is not focused, and
        `#1a2b3c · Researcher` there is what stops a reader from typing into the
        wrong one.
        """
        base = window_title.compose(self.repository_label(), self._session.display_title)
        node = getattr(self, 'viewed_agent', None)
        if callable(node):
            viewing = node()
            if viewing is not None:
                return f'{viewing.label} #{viewing.short_id} · {base}'
        return base

    def paint_window_title(self) -> str:
        """Writes the title to the terminal and returns it.

        The value is also kept on the app (``_painted_title``): the terminal cannot
        be read back, so this is the only place a test or a diagnostic can see what
        was written.
        """
        title = self.window_title_text()
        self._painted_title = title
        window_title.apply(getattr(self, '_driver', None), title)
        return title

    # --- naming -------------------------------------------------------------

    def should_name_session(self, session: Any) -> bool:
        """Whether this session is still waiting for a title.

        A session gets one title and keeps it: a title that followed every message
        would rename the window while the conversation diverges, which is the
        opposite of what a label is for.

        A request already on its way counts as answered, so a slow title plus a
        second turn costs one request and not two. The worker reports its own state,
        so a cancelled request stops counting without anything to clean up.
        """
        if getattr(session, 'title', None):
            return False
        worker = self._naming_workers.get(session.id)
        # `is_cancelled` is set the moment the group is cancelled, while the task
        # itself may still be winding down: a cancelled request will not deliver a
        # title, so it must not block the next attempt.
        if worker is not None and worker.is_running and not worker.is_cancelled:
            return False
        return self._naming_attempts.get(session.id, 0) < MAX_NAMING_ATTEMPTS

    def name_session(self, session: Any, text: str) -> None:
        """Asks the model for a title for ``session``, if it needs one.

        ``text`` is the message of the turn starting now. The first turn names the
        conversation from it; a later attempt, after one that failed, names the
        conversation from the message it actually started with.
        """
        if not self.should_name_session(session):
            return
        source = first_user_message(getattr(session, 'messages', None) or []) or str(text or '').strip()
        if not source:
            return
        self._naming_attempts[session.id] = self._naming_attempts.get(session.id, 0) + 1
        self._naming_workers[session.id] = self.generate_session_title(session, source)

    @work(group='session-title')
    async def generate_session_title(self, session: Any, source: str) -> None:
        """Generates the title and writes it to the session, the disk and the window.

        The model is the one the agent holds, which is the router: whatever account
        the session was already configured with answers, so naming a conversation
        needs no provider of its own.
        """
        model = getattr(self._agent, 'model', None)
        try:
            title = await generate_title(model, source)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a name is never worth failing a turn over
            return
        if not title:
            return

        session.title = title
        with suppress(Exception):
            # The turn writes its own checkpoints; a failed save here costs the
            # title on the next start, not the conversation.
            self._store.save(session)
        # A title that arrives after the user moved on belongs to the session it
        # names: it is saved there, and the window keeps describing the active one.
        if session is self._session:
            self.paint_window_title()
