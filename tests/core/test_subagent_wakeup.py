"""A finished subagent wakes the agent that summoned it.

The turn that dispatched the squad ends — the user read its answer and the session
went back to idle — and the subagent's result arrives afterwards. Waiting for the
user to type something would leave the squad's work unread, so the app starts the
turn the answer is owed, and it is exactly one turn no matter how many reports land
inside the debounce window.

Three things are pinned here:

- the session wakes on its own, with the reports prepended;
- three reports inside the window produce **one** turn, not three;
- a report that arrives while the app is closed is not lost: the flag that says
  "nobody read this" travels with the session.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from src.core.agents.profile.agent_registry import AgentRegistry
from src.core.agents.profile.models.agent_role import AgentRole
from src.core.agents.tree.agent_tree_store import AgentTreeStore
from src.core.agents.tree.subagent_summoner import SubagentSummoner
from src.core.sessions.session_store import SessionStore


def _summoner(tmp: str, tree: AgentTreeStore) -> SubagentSummoner:
    registry = AgentRegistry([('builtin', Path(tmp) / 'none')], active=None)
    return SubagentSummoner(trees=tree, registry=registry, builder=lambda: lambda profile: None)


# --- the engine side ---------------------------------------------------------


def test_an_answer_with_nobody_in_a_turn_stays_undelivered() -> None:
    tree = AgentTreeStore()
    with tempfile.TemporaryDirectory() as tmp:
        summoner = _summoner(tmp, tree)
        session = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions').create_session()
        tree.bind(session, root_name='wizard')
        tree.active.ensure_root('wizard')
        node = tree.active.add(parent_id=tree.active.root_id, name='researcher', role=AgentRole.SUBAGENT)
        node.finish('done')
        node.delivered = False

        assert [candidate.id for candidate in summoner.waiting()] == [node.id]
        messages = summoner.take_messages()
        assert len(messages) == 1 and 'done' in messages[0]
        # Consumed: a second turn does not read it again.
        assert summoner.waiting() == []
        assert summoner.take_messages() == []


def test_the_undelivered_flag_survives_a_save_and_a_load(tmp_path: Path) -> None:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
    session = store.create_session()
    tree = AgentTreeStore()
    trees = tree.bind(session, root_name='wizard')
    node = trees.add(parent_id=trees.root_id, name='researcher', role=AgentRole.SUBAGENT, task='read')
    node.finish('a report')
    node.delivered = False
    store.save(session)

    loaded = store.load(session.id)

    assert loaded is not None and loaded.tree is not None
    restored = loaded.tree.node(node.id)
    assert restored is not None
    assert restored.delivered is False
    assert restored.result == 'a report'
