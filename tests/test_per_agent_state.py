"""What belongs to one agent and not to the session: its plan and its skills.

Two pieces of state used to be session-wide, and both of them lied once a tree
existed:

- **The roadmap.** A plan belongs to whoever executes it. The Lead's "what is the
  squad doing" is not the engineer's "which unit am I in", and one list under both
  reads wrong to at least one of them. It is stored, keyed by execution, because a
  task can be re-opened without a new tool call.
- **The loaded skills.** A loaded skill injects its whole body into the
  instructions, and the instructions of another task do not belong in this one. It
  is *not* stored: it is derived from the transcript of each execution, which is
  what makes a resumed session keep the set it was following.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from src.engine.agents.agent import create_agent
from src.engine.agents.tree.summoner import SigilDeps
from src.engine.roadmap.store import RoadmapStore
from src.engine.roadmap.stores import RoadmapStores
from src.engine.sessions.session_store import SessionStore
from src.engine.skills.loaded import loaded_skills
from src.engine.skills.registry import SkillRegistry


def _item(title: str):
    from src.engine.roadmap.models.todo_item import TodoItem

    return TodoItem(title=title)


def _store(tmp_path: Path) -> SessionStore:
    return SessionStore(cwd=tmp_path / 'ws', base_dir=tmp_path)


def _session(tmp_path: Path):
    """A stored session: a plan needs an execution to belong to."""
    return _store(tmp_path).create_session()


def test_each_node_gets_its_own_plan(tmp_path: Path):
    session = _session(tmp_path)
    stores = RoadmapStores(session_id=session.id, base_dir=tmp_path)

    root = stores.for_node(None)
    first = stores.for_node('aaaa1111')
    second = stores.for_node('bbbb2222')

    assert root is not first and first is not second
    root.set_items([_item('plan the squad')])
    first.set_items([_item('implement unit 1')])

    assert [item.title for item in root.snapshot()] == ['plan the squad']
    assert [item.title for item in first.snapshot()] == ['implement unit 1']
    assert second.snapshot() == []


def test_the_root_plan_belongs_to_the_root_execution(tmp_path: Path):
    """No sentinel and no `NULL`: the root has an id like any other execution."""
    session = _session(tmp_path)
    stores = RoadmapStores(session_id=session.id, base_dir=tmp_path)

    assert stores.for_node(None).execution_id == session.root_id
    assert stores.for_node('abcdef1234567890').execution_id == 'abcdef1234567890'
    # The derived id is the one the store wrote for the root execution.
    assert stores.execution_id_of(None) == session.root_id


def test_a_plan_written_by_a_node_is_read_back_by_that_node(tmp_path: Path):
    store = _store(tmp_path)
    session = store.create_session()
    # A node whose execution the session already knows, which is the real order: the
    # tree is saved with the session before its plan is read again.
    store.database.execute(
        'INSERT INTO agent_executions (id, session_id, parent_id, name, role, depth, status, started_at) '
        "VALUES ('aaaa1111', ?, ?, 'scout', 'subagent', 1, 'running', 0)",
        (session.id, session.root_id),
    )
    first = RoadmapStores(session_id=session.id, base_dir=tmp_path)
    first.for_node('aaaa1111').set_items([_item('read the tree')])

    second = RoadmapStores(session_id=session.id, base_dir=tmp_path)

    assert [item.title for item in second.for_node('aaaa1111').snapshot()] == ['read the tree']
    assert second.for_node(None).snapshot() == []


def test_a_plan_written_before_its_execution_exists_is_flushed(tmp_path: Path):
    """A subagent lives in the tree before it is a row; ``flush`` closes the gap."""
    session = _session(tmp_path)
    flushes: list[int] = []
    stores = RoadmapStores(
        session_id=session.id,
        base_dir=tmp_path,
        flush=lambda: (flushes.append(1), session.tree),
    )
    # The node is not a row yet, so the first write cannot land.
    orphan = stores.for_node('cccc3333')
    orphan.set_items([_item('planned early')])

    assert flushes, 'the store asked the app to persist the session'
    assert orphan.persistence_error is not None


def test_switching_session_drops_the_plans_of_the_old_one(tmp_path: Path):
    first = _session(tmp_path)
    second = _session(tmp_path)
    stores = RoadmapStores(session_id=first.id, base_dir=tmp_path)
    stores.for_node(None).set_items([_item('old plan')])

    stores.bind_session(second.id)

    assert stores.for_node(None).snapshot() == []
    assert stores.for_node(None).execution_id == second.root_id


def test_discarding_a_node_removes_its_plan(tmp_path: Path):
    store = _store(tmp_path)
    session = store.create_session()
    store.database.execute(
        'INSERT INTO agent_executions (id, session_id, parent_id, name, role, depth, status, started_at) '
        "VALUES ('aaaa1111', ?, ?, 'scout', 'subagent', 1, 'running', 0)",
        (session.id, session.root_id),
    )
    stores = RoadmapStores(session_id=session.id, base_dir=tmp_path)
    store = stores.for_node('aaaa1111')
    store.set_items([_item('gone')])
    assert [item.title for item in store.snapshot()] == ['gone']

    stores.discard('aaaa1111')

    assert store.snapshot() == []
    assert store.database.value('SELECT count(*) FROM roadmap_items WHERE execution_id = ?', ('aaaa1111',)) == 0


def test_the_store_notifies_through_the_registry(tmp_path: Path):
    session = _session(tmp_path)
    stores = RoadmapStores(session_id=session.id, base_dir=tmp_path)
    heard: list[int] = []
    stores.add_listener(lambda: heard.append(1))

    stores.for_node(None).set_items([_item('a')])

    assert heard


# --- skills ------------------------------------------------------------------


def _load_call(name: str, *, call_id: str = 'call-1', outcome: str = 'success') -> list:
    """A history whose last turn loaded ``name``."""
    return [
        ModelResponse(parts=[ToolCallPart(tool_name='load_skill', args={'name': name}, tool_call_id=call_id)]),
        ModelRequest(
            parts=[ToolReturnPart(tool_name='load_skill', content='ok', tool_call_id=call_id, outcome=outcome)]
        ),
    ]


def test_the_set_is_derived_per_execution_not_per_session():
    """Two executions of the same session have their own sets, by construction.

    There is no shared registry to keep in step: each one is a walk over its own
    transcript, so a subagent cannot inherit what its parent loaded.
    """
    parent = _load_call('archify', call_id='p1') + _load_call('clean-code', call_id='p2')
    child = _load_call('clean-md', call_id='c1')

    assert loaded_skills(parent) == ('archify', 'clean-code')
    assert loaded_skills(child) == ('clean-md',)
    assert loaded_skills([]) == ()


def test_the_load_order_is_the_order_of_the_calls():
    history = _load_call('beta', call_id='b') + _load_call('alpha', call_id='a')
    assert loaded_skills(history) == ('beta', 'alpha')


def test_a_failed_load_is_not_in_the_set():
    """`ToolErrorRecovery` returns a failure as a `ToolReturnPart` with `outcome`."""
    assert loaded_skills(_load_call('ghost', outcome='failed')) == ()


def test_a_call_without_its_return_does_not_count():
    """An interrupted call never delivered the body, so the body is not in effect."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    history = [ModelResponse(parts=[ToolCallPart(tool_name='load_skill', args={'name': 'demo'}, tool_call_id='x')])]
    assert loaded_skills(history) == ()


def test_a_resumed_history_keeps_the_skills_it_had_loaded(tmp_path: Path):
    """The bug this design removes: a resumed session lost the set and said nothing.

    The instructions used to drop from 73,892 to 29,476 characters across a restart
    because the registry lived in memory and was never serialized. A projection
    cannot do that: the log came back, so the set came back.
    """
    write_skill(tmp_path, 'alpha', body='# Alpha body')
    registry = SkillRegistry([('project', tmp_path)])
    agent = _agent(registry)

    history = _load_call('alpha')
    root_text = _instructions(agent, None, history)

    assert '# Alpha body' in root_text


def test_a_subagent_does_not_see_the_skills_of_its_parent(tmp_path: Path):
    """The block is instructions: the ones for another task do not belong here."""
    write_skill(tmp_path, 'alpha', body='# Alpha body')
    write_skill(tmp_path, 'beta', body='# Beta body')
    registry = SkillRegistry([('project', tmp_path)])

    agent = _agent(registry)

    root_text = _instructions(agent, None, _load_call('alpha', call_id='a'))
    child_text = _instructions(agent, 'child1', _load_call('beta', call_id='b'))

    assert '# Alpha body' in root_text
    assert '# Beta body' not in root_text
    assert '# Beta body' in child_text
    assert '# Alpha body' not in child_text


# --- helpers ------------------------------------------------------------------


def write_skill(root: Path, name: str, *, body: str = 'Body.') -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'SKILL.md'
    path.write_text(
        f'---\nname: {name}\ndescription: The {name} skill.\n---\n\n{body}\n',
        encoding='utf-8',
    )
    return path


def _agent(registry: SkillRegistry):
    """One agent object, shared by every node: that is how the app builds them."""

    def model(messages, info) -> ModelResponse:
        return ModelResponse(parts=[TextPart('ok')])

    return create_agent(registry, model=FunctionModel(model))


def _instructions(agent, node_id: str | None, history: list | None = None) -> str:
    """The instructions of one run of that agent, for the node it belongs to."""
    from pydantic_ai.messages import ModelRequest

    result = asyncio.run(agent.run('go', deps=SigilDeps(node_id=node_id), message_history=history))
    requests = [message for message in result.all_messages() if isinstance(message, ModelRequest)]
    return (requests[-1].instructions or '') if requests else ''


class _Ctx:
    """The minimum of a `RunContext` the skills tools read: their node."""

    def __init__(self, node_id: str | None) -> None:
        self.deps = SigilDeps(node_id=node_id)


def _ctx(node_id: str | None) -> _Ctx:
    return _Ctx(node_id)


def _tool(agent, name: str):
    raw = agent._function_toolset.tools[name].function
    return raw


def _unused(store: RoadmapStore) -> RoadmapStore:  # pragma: no cover
    return store
