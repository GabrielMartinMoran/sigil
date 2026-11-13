"""A woken turn is a report, not something the reader typed.

A subagent's answer reaches the parent as an ordinary `user-prompt` part — the
envelope followed by the wake sentence — because that is what makes it a durable row
that survives a crash. The consequence lands on the projection: a client that paints
the items has no tree, so the recognition has to happen where the items are built.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from src.engine.agents.tree.models.agent_node import AgentNode, NodeStatus
from src.engine.agents.tree.reports import WAKE_PROMPT, split_reports
from src.engine.agents.tree.summoner import _envelope
from src.server import views


def _node(result: str = 'the report body', status: NodeStatus = NodeStatus.DONE) -> AgentNode:
    return AgentNode(
        id='node-1',
        name='scout',
        role='subagent',  # type: ignore[arg-type]
        parent_id='root',
        depth=1,
        task='read the tree',
        status=status,
        result=result,
    )


def _wake_prompt(node: AgentNode) -> str:
    return f'{_envelope(node, node.result or "")}\n\n{WAKE_PROMPT}'


def _turn(text: str) -> list[object]:
    return [ModelRequest(parts=[UserPromptPart(content=text)])]


def test_a_woken_turn_projects_as_a_report_and_not_as_a_user_message() -> None:
    """The squad's words are the squad's: they are not painted as the reader's."""
    node = _node()
    items = views.transcript(_turn(_wake_prompt(node)), reports=(node,))

    assert [item['kind'] for item in items] == ['subagent_report']
    report = items[0]
    assert report['node_id'] == node.id
    assert report['short_id'] == node.short_id
    assert report['label'] == node.label
    assert report['text'] == 'the report body'
    assert report['index'] == 0


def test_the_wake_sentence_does_not_survive_as_a_message_of_its_own() -> None:
    """The wake prompt is the service talking to itself, not a turn in the log."""
    node = _node()
    items = views.transcript(_turn(_wake_prompt(node)), reports=(node,))

    assert all(item.get('text') != WAKE_PROMPT for item in items)
    assert 'user' not in [item['kind'] for item in items]


def test_text_the_user_typed_is_never_swallowed_by_the_recognition() -> None:
    """Recognition is an exact match against a saved result, not a marker."""
    node = _node()
    typed = f'look at this: {_envelope(node, node.result or "")}'
    items = views.transcript(_turn(typed), reports=(node,))

    assert [item['kind'] for item in items] == ['user']
    assert items[0]['text'] == typed


def test_without_the_tree_a_woken_turn_is_projected_as_it_was() -> None:
    """A caller that passes no nodes keeps the answer it already had."""
    node = _node()
    items = views.transcript(_turn(_wake_prompt(node)))

    assert [item['kind'] for item in items] == ['user']


def test_a_report_followed_by_a_real_message_keeps_both() -> None:
    """The report is recognized and the rest is still the user's line."""
    node = _node()
    text = f'{_wake_prompt(node)}\n\nand now fix the tests'
    items = views.transcript(_turn(text), reports=(node,))

    assert [item['kind'] for item in items] == ['subagent_report', 'user']
    assert items[1]['text'] == 'and now fix the tests'


def test_the_wake_sentence_alone_is_not_a_report() -> None:
    """With nothing recognized, the sentence is left where it is."""
    node = _node(result='something else entirely')
    items = views.transcript(_turn(WAKE_PROMPT), reports=(node,))

    assert [item['kind'] for item in items] == ['user']
    assert items[0]['text'] == WAKE_PROMPT


def _conversation(node: AgentNode) -> list[object]:
    return [
        ModelRequest(parts=[UserPromptPart(content='a question')]),
        ModelResponse(parts=[TextPart(content='an answer')]),
        ModelRequest(parts=[UserPromptPart(content=_wake_prompt(node))]),
        ModelRequest(parts=[UserPromptPart(content='after the report')]),
        ModelResponse(parts=[TextPart(content='a second answer')]),
    ]


@pytest.mark.parametrize('limit', [None, 1, 2, 3, 4])
@pytest.mark.parametrize('before', [None, 0, 1, 2, 3, 4, 5, 99])
def test_a_window_agrees_with_the_whole_projection_about_reports(limit: int | None, before: int | None) -> None:
    """Paging counts report items, so the window and the whole read stay equal."""
    node = _node()
    messages = _conversation(node)
    whole = views.transcript(messages, reports=(node,))  # type: ignore[arg-type]
    oracle = views.transcript_page(whole, limit=limit, before=before)
    window = views.transcript_window(messages, limit=limit, before=before, reports=(node,))  # type: ignore[arg-type]

    assert window.items == oracle.items
    assert window.first_index == oracle.first_index
    assert window.has_more == oracle.has_more
    assert window.total == oracle.total


def test_the_split_reports_only_what_matches_and_returns_the_rest() -> None:
    """The engine rule on its own: the envelope goes, the tail stays."""
    node = _node()
    reports, remaining = split_reports(f'{_wake_prompt(node)}\ntrailing words', [node])

    assert [found.id for found in reports] == [node.id]
    assert remaining == 'trailing words'
