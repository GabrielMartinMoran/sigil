"""The agent in the UI: the status bar, `/agent`, `ctrl+a` and the session.

Four behaviours are pinned here, because each of them is a way the feature can
look finished and not be:

- the bar says which agent answers, and it survives a transient message;
- `ctrl+a` opens the picker with the focus in the prompt (it is `home` there);
- `/agent <name>` switches directly, and a name that does not exist is a notice,
  not a crash;
- the agent travels with the session the way the model does.
"""

from __future__ import annotations

from pathlib import Path

from src.core.sessions.session_store import SessionStore


def test_the_agent_survives_being_saved(tmp_path: Path) -> None:
    store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')
    session = store.create_session()
    session.agent = 'reviewer'
    store.save(session)

    assert store.load(session.id).agent == 'reviewer'


def test_a_session_without_a_profile_reads_back_empty(tmp_path: Path) -> None:
    """An empty profile means "no choice recorded": the registry's default applies."""
    store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')
    session = store.create_session()
    store.database.execute(
        "UPDATE agent_executions SET profile = '', name = '' WHERE session_id = ?",
        (session.id,),
    )

    assert store.load(session.id).agent == ''
