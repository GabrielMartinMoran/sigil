"""Message origin must come from persisted metadata, never message text."""

from pydantic_ai.messages import ModelRequest, UserPromptPart

from src.core.agents.tree.models.agent_node import AgentNode
from src.core.agents.tree.models.node_status import NodeStatus
from src.core.agents.tree.reports import WAKE_PROMPT
from src.core.agents.tree.subagent_summoner import _envelope
from src.server.api.views import transcript, transcript_window


def test_untyped_historical_callback_defaults_to_user() -> None:
    node = AgentNode(
        id='node-1',
        name='scout',
        role='subagent',
        parent_id='root',
        depth=1,
        task='investigate',
        status=NodeStatus.DONE,
        result='report',
    )
    text = f'{_envelope(node, "report")}\n\n{WAKE_PROMPT}'
    messages = [ModelRequest(parts=[UserPromptPart(content=text)])]
    assert transcript(messages, reports=(node,))[0]['kind'] == 'user'
    assert transcript_window(messages, reports=(node,)).items[0]['kind'] == 'user'
