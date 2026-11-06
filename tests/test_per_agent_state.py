"""What belongs to one agent and not to the session: its plan and its skills.

Two pieces of state used to be session-wide, and both of them lied once a tree
existed:

- **The roadmap.** A plan belongs to whoever executes it. The Lead's "what is the
  squad doing" is not the engineer's "which unit am I in", and one list under both
  reads wrong to at least one of them.
- **The loaded skills.** A loaded skill injects its whole body into the
  instructions. Sharing the registry would put the Lead's design guidance into an
  engineer's prompt and spend its budget on it.

Both are keyed by node here, with ``None`` for the root, and both survive a session
swap without leaking into the next conversation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from src.engine.agents.agent import create_agent
from src.engine.agents.tree.summoner import SigilDeps
from src.engine.roadmap.store import RoadmapStore
from src.engine.roadmap.stores import RoadmapStores
from src.engine.skills.loaded import LoadedSkills
from src.engine.skills.loads import SkillLoads
from src.engine.skills.registry import SkillRegistry
from src.engine.skills.tools import register_skill_tools


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


def test_each_node_has_its_own_loaded_skills():
    loads = SkillLoads()

    loads.for_node('aaaa1111').add('clean-code')
    loads.for_node(None).add('archify')

    assert loads.names('aaaa1111') == ('clean-code',)
    assert loads.names(None) == ('archify',)
    # A node that loaded nothing has nothing: no inheritance in either direction.
    assert loads.names('bbbb2222') == ()


def test_a_subagent_does_not_see_the_skills_of_its_parent(tmp_path: Path):
    """The block is instructions: the ones for another task do not belong here."""
    write_skill(tmp_path, 'alpha', body='# Alpha body')
    write_skill(tmp_path, 'beta', body='# Beta body')
    registry = SkillRegistry([('project', tmp_path)])
    registry.refresh()
    loads = SkillLoads()
    loads.for_node(None).add('alpha')
    loads.for_node('child1').add('beta')

    agent = _agent(registry, loads)

    root_text = _instructions(agent, None)
    child_text = _instructions(agent, 'child1')

    assert '# Alpha body' in root_text
    assert '# Beta body' not in root_text
    assert '# Beta body' in child_text
    assert '# Alpha body' not in child_text


def test_loading_a_skill_registers_it_on_the_node_of_the_run(tmp_path: Path):
    write_skill(tmp_path, 'alpha', body='# Alpha body')
    registry = SkillRegistry([('project', tmp_path)])
    loads = SkillLoads()
    agent = _agent(registry, loads)
    register_skill_tools(agent, registry, loaded=loads)

    tool = _tool(agent, 'load_skill')
    tool(_ctx('child1'), 'alpha')

    assert loads.names('child1') == ('alpha',)
    assert loads.names(None) == ()


def test_the_loads_travel_with_the_session(tmp_path: Path):
    loads = SkillLoads()
    loads.for_node(None).add('archify')
    loads.for_node('aaaa1111').add('clean-code')

    restored = SkillLoads()
    restored.restore(loads.to_dict())

    assert restored.names(None) == ('archify',)
    assert restored.names('aaaa1111') == ('clean-code',)


def test_a_broken_stored_entry_does_not_break_the_rest():
    loads = SkillLoads()
    loads.restore({'': ['ok'], 'node': 'not-a-list', 123: ['x']})

    assert loads.names(None) == ('ok',)


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


def _agent(registry: SkillRegistry, loads: SkillLoads):
    """One agent object, shared by every node: that is how the app builds them."""

    def model(messages, info) -> ModelResponse:
        return ModelResponse(parts=[TextPart('ok')])

    return create_agent(
        registry,
        model=FunctionModel(model),
        loaded_skills=loads.for_node,
    )


def _instructions(agent, node_id: str | None) -> str:
    """The instructions of one run of that agent, for the node it belongs to."""
    result = asyncio.run(agent.run('go', deps=SigilDeps(node_id=node_id)))
    return result.all_messages()[0].instructions or ''


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


def _unused_loaded(loaded: LoadedSkills) -> LoadedSkills:  # pragma: no cover
    return loaded
