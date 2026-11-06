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
from src.engine.skills.loaded import loaded_skills
from src.engine.skills.registry import SkillRegistry


def test_each_node_gets_its_own_roadmap(tmp_path: Path):
    stores = RoadmapStores(directory=tmp_path, session_id='session1')

    root = stores.for_node(None)
    first = stores.for_node('aaaa1111')
    second = stores.for_node('bbbb2222')

    assert root is not first and first is not second
    root.set_items([_item('plan the squad')])
    first.set_items([_item('implement unit 1')])

    assert [item.title for item in root.snapshot()] == ['plan the squad']
    assert [item.title for item in first.snapshot()] == ['implement unit 1']
    assert second.snapshot() == []


def test_the_root_keeps_the_session_file_and_a_node_gets_its_own(tmp_path: Path):
    stores = RoadmapStores(directory=tmp_path, session_id='session1')

    assert stores.for_node(None).path == tmp_path / 'session1.json'
    assert stores.for_node('abcdef1234567890').path == tmp_path / 'session1--abcdef12.json'


def test_a_roadmap_written_by_a_node_is_read_back_by_that_node(tmp_path: Path):
    first = RoadmapStores(directory=tmp_path, session_id='session1')
    first.for_node('aaaa1111').set_items([_item('read the tree')])

    second = RoadmapStores(directory=tmp_path, session_id='session1')

    assert [item.title for item in second.for_node('aaaa1111').snapshot()] == ['read the tree']
    assert second.for_node(None).snapshot() == []


def test_switching_session_drops_the_roadmaps_of_the_old_one(tmp_path: Path):
    stores = RoadmapStores(directory=tmp_path, session_id='session1')
    stores.for_node('aaaa1111').set_items([_item('old plan')])

    stores.bind_session('session2')

    assert stores.for_node('aaaa1111').snapshot() == []
    assert stores.for_node('aaaa1111').path == tmp_path / 'session2--aaaa1111.json'


def test_discarding_a_node_removes_its_file(tmp_path: Path):
    stores = RoadmapStores(directory=tmp_path, session_id='session1')
    store = stores.for_node('aaaa1111')
    store.set_items([_item('gone')])
    assert store.path is not None and store.path.exists()

    stores.discard('aaaa1111')

    assert store.path is not None and not store.path.exists()


def test_the_store_notifies_through_the_registry(tmp_path: Path):
    stores = RoadmapStores(directory=tmp_path, session_id='session1')
    heard: list[int] = []
    stores.add_listener(lambda: heard.append(1))

    stores.for_node('aaaa1111').set_items([_item('a')])

    assert heard


def _item(title: str):
    from src.engine.roadmap.models.todo_item import TodoItem

    return TodoItem(title=title)


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
