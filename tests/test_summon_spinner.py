from src.ui.widgets.chat_log import SummonBlock
from src.ui.widgets.status_bar import SPINNER_FRAMES


def test_pending_summon_animates_and_stops_when_finished():
    block = SummonBlock('summon', {'agent': 'scout', 'task': 'inspect'})
    assert SPINNER_FRAMES[0] in block._spawn_header().plain
    block._advance_pending()
    assert SPINNER_FRAMES[1] in block._spawn_header().plain

    block.set_result('[subagent Scout (scout #abcd1234, sync) finished]')
    assert not any(frame in block._spawn_header().plain for frame in SPINNER_FRAMES)
    assert 'finished' in block._meta_line().plain


def test_pending_resume_also_uses_spinner():
    block = SummonBlock('summon', {'agent_id': 'abcd1234', 'task': 'continue'})
    assert SPINNER_FRAMES[0] in block._spawn_header().plain
    assert 'resume' in block._spawn_header().plain


def test_background_summon_tracks_node_until_completion():
    from src.engine.agents.profile.models.agent_role import AgentRole
    from src.engine.agents.tree.store import AgentTreeStore

    tree = AgentTreeStore().active
    root = tree.ensure_root('lead')
    node = tree.add(parent_id=root.id, name='scout', role=AgentRole.SUBAGENT, task='inspect')
    node.start()
    block = SummonBlock('summon', {'agent': 'scout', 'mode': 'async', 'task': 'inspect'})
    block.set_result(f'#{node.short_id} (Scout) is working in the background.')
    block.set_node(node)
    assert SPINNER_FRAMES[0] in block._spawn_header().plain
    block._advance_pending()
    assert SPINNER_FRAMES[1] in block._spawn_header().plain
    node.finish('done')
    block.set_node(node)
    assert 'finished' in block._meta_line().plain
    assert not any(frame in block._spawn_header().plain for frame in SPINNER_FRAMES)
    node.start()
    block.set_node(node)
    assert 'finished' in block._meta_line().plain
