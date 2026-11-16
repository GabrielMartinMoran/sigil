from src.core.agents.notice import notice
from src.core.agents.origin import origin
from src.core.agents.profile.models.agent_role import AgentRole
from src.core.agents.request import request
from src.core.agents.tree.agent_tree_store import AgentTreeStore
from src.server.views import node_view


def test_subagent_projection_distinguishes_instructions_and_internal_budget_notice() -> None:
    tree = AgentTreeStore().active
    root = tree.ensure_root('lead')
    node = tree.add(parent_id=root.id, name='scout', role=AgentRole.SUBAGENT, task='inspect')
    node.messages = [
        request('inspect', origin('agent_message')),
        request(notice(2, role='subagent'), origin('internal_notice')),
    ]
    items = node_view(node, include_messages=True)['transcript']
    assert [item['kind'] for item in items] == ['agent_message']
