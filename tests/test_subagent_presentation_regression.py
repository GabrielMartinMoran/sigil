import asyncio

from src.engine.agents.message_types import origin, request
from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.step_budget import notice
from src.engine.agents.tree.store import AgentTreeStore
from src.server.views import node_view
from src.ui.widgets.status_bar import StatusBar


def test_subagent_projection_distinguishes_instructions_and_internal_budget_notice():
    tree = AgentTreeStore().active
    root = tree.ensure_root('lead')
    node = tree.add(parent_id=root.id, name='scout', role=AgentRole.SUBAGENT, task='inspect')
    node.messages = [
        request('inspect', origin('agent_message')),
        request(notice(2, role='subagent'), origin('internal_notice')),
    ]
    items = node_view(node, include_messages=True)['transcript']
    assert [item['kind'] for item in items] == ['agent_message']


def test_status_bar_counts_background_subagents_at_rest():
    asyncio.run(_check_status_bar())


async def _check_status_bar():
    bar = StatusBar()
    bar.set_subagents(2)
    assert 'Ready' in bar.plain()
    assert '2 subagents running' in bar.plain()
    bar.set_subagents(1)
    assert '1 subagent running' in bar.plain()
    bar.set_subagents(0)
    assert 'subagent' not in bar.plain()
    bar.on_unmount()
