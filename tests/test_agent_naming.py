"""How a subagent is named, in every place its name is painted.

Two defects met here, and the second one hid the first:

- **The name was painted twice.** The tree row, the node header and the spawn block
  showed the humanized label (`Pulsar Researcher`) *and* the profile name
  (`pulsar-researcher`). They are the same string — the label is the name with dashes
  turned into spaces — so the second one was pure noise.
- **The block lost the name on replay.** `ToolCallPart.args` is a `dict` in a live
  turn and a JSON *string* once the history is validated, and the spawn block only
  understood the dict. Reopening a session painted `▶ Subagent` instead of the agent
  that was actually started, which is also how "Subagent" ended up on screen.

So: one name per surface, and the name survives a round trip through disk.
"""

from __future__ import annotations

import json

from pydantic_ai.messages import ModelMessagesTypeAdapter

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.tree.store import AgentTreeStore
from src.ui.widgets.agent_tree_screen import node_row
from src.ui.widgets.agent_view import build_agent_header
from src.ui.widgets.chat_log import SummonBlock
from tests.ui_helpers import render_text


def _text_of(block: SummonBlock) -> str:
    return '\n'.join(part.plain for part in block._build().renderables)


def _node():
    tree = AgentTreeStore().active
    root = tree.ensure_root('pulsar-lead')
    node = tree.add(
        parent_id=root.id,
        name='pulsar-researcher',
        role=AgentRole.SUBAGENT,
        task='read the tree',
    )
    node.mode = 'async'
    node.finish('done')
    return node


# --- the block, live and replayed ---------------------------------------------


def test_the_block_names_the_agent_once():
    block = SummonBlock('summon', {'agent': 'pulsar-researcher', 'task': 'read it', 'mode': 'async'})
    text = _text_of(block)

    assert 'Pulsar Researcher' in text
    # The raw profile name is the same datum: painting it too is noise.
    assert text.count('Pulsar Researcher') == 1
    assert 'pulsar-researcher' not in text


def test_the_block_reads_the_name_out_of_a_json_string():
    """A history replayed from disk carries the arguments as a JSON string."""
    raw = [
        {
            'kind': 'response',
            'parts': [
                {
                    'part_kind': 'tool-call',
                    'tool_name': 'summon',
                    'args': json.dumps({'agent': 'pulsar-researcher', 'task': 'read it', 'mode': 'async'}),
                    'tool_call_id': 'x',
                }
            ],
        }
    ]
    part = ModelMessagesTypeAdapter.validate_python(raw)[0].parts[0]
    assert isinstance(part.args, str), 'the case this test exists for'

    block = SummonBlock('summon', part.args)
    text = _text_of(block)

    assert 'Pulsar Researcher' in text
    assert 'read it' in text
    assert 'async' in text
    assert 'Subagent' not in text


def test_a_resume_takes_the_name_from_the_result():
    """`summon(agent_id=…)` carries no name, and the answer knows it."""
    block = SummonBlock('summon', {'task': 'keep going', 'agent_id': '1a2b3c4d'})
    block.set_result('[subagent Pulsar Researcher (pulsar-researcher #1a2b3c4d, sync) finished]')

    text = _text_of(block)
    assert 'Pulsar Researcher' in text
    assert 'resume' in text
    assert 'Subagent' not in text


def test_a_block_with_nothing_to_go_on_does_not_invent_a_name():
    """No arguments and no result: say a subagent, not the word `subagent` twice."""
    block = SummonBlock('summon', {})
    text = _text_of(block)

    assert 'Subagent' not in text
    assert 'subagent' not in text


# --- the tree row and the header ----------------------------------------------


def test_the_tree_row_names_the_agent_once():
    row = render_text(node_row(_node()).prompt)

    assert 'Pulsar Researcher' in row
    assert 'pulsar-researcher' not in row
    assert '#676031c4'[:1] in row or '#' in row


def test_the_node_header_names_the_agent_once():
    text = render_text(build_agent_header(_node(), parent_label='Pulsar Lead', has_parent=True))

    assert text.count('Pulsar Researcher') == 1
    assert 'pulsar-researcher' not in text


def test_a_refused_resume_keeps_the_id_and_mode():
    block = SummonBlock('summon', {'agent_id': '#ea0c3711', 'mode': 'async', 'task': 'continue'})
    block.set_result('PermissionDenied: no', ok=False)
    text = _text_of(block)
    assert 'resume #ea0c3711' in text
    assert 'async' in text
    assert '##' not in text
