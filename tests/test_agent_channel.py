"""The frontmatter of a subagent: its budget, and which way it may talk.

Two declarations, tested the way they fail in use:

- **`max_steps`.** A budget that is read as a number when it is a string, or one of
  zero, leaves a profile that cannot answer — so both are rejected while parsing.
- **`channel`.** The default is unidirectional, the short form is a string, and the
  long form names the parent and the siblings. Half-declared is an error: a channel
  that silently degrades would leave the profile promising a conversation it cannot
  have.
"""

from __future__ import annotations

import pytest

from src.engine.agents.profile.models.agent_parse_error import AgentParseError
from src.engine.agents.profile.models.channel import ChannelMode, ChannelPolicy, parse_channel
from src.engine.agents.profile.parser import parse_profile

FRONTMATTER = '---\nname: scout\ndescription: reads\n{extra}---\nbody\n'


def _write(tmp_path, extra: str):
    path = tmp_path / 'scout.md'
    path.write_text(FRONTMATTER.format(extra=extra), encoding='utf-8')
    return path


# --- max_steps ---------------------------------------------------------------


def test_a_profile_without_a_budget_uses_the_global_one(tmp_path):
    profile, _ = parse_profile(_write(tmp_path, ''), source='project')
    assert profile.max_steps is None


def test_a_profile_can_declare_its_own_budget(tmp_path):
    profile, _ = parse_profile(_write(tmp_path, 'max_steps: 12\n'), source='project')
    assert profile.max_steps == 12


@pytest.mark.parametrize('value', ['0', '-3'])
def test_a_budget_that_cannot_answer_is_rejected(tmp_path, value):
    with pytest.raises(AgentParseError, match='at least 1'):
        parse_profile(_write(tmp_path, f'max_steps: {value}\n'), source='project')


def test_a_budget_that_is_not_a_number_is_rejected(tmp_path):
    with pytest.raises(AgentParseError, match='positive integer'):
        parse_profile(_write(tmp_path, 'max_steps: "12"\n'), source='project')


# --- channel -----------------------------------------------------------------


def test_a_profile_without_a_channel_is_unidirectional():
    policy = parse_channel(None)
    assert policy.mode is ChannelMode.UNIDIRECTIONAL
    assert not policy.bidirectional
    assert not policy.may_message_parent()
    assert not policy.may_message_sibling('anything')


def test_the_short_form_declares_the_mode(tmp_path):
    profile, _ = parse_profile(_write(tmp_path, 'channel: bidirectional\n'), source='project')
    assert profile.channel.bidirectional
    assert profile.channel.may_message_parent()
    assert profile.channel.to_siblings == ()


def test_the_long_form_declares_the_neighbours(tmp_path):
    extra = 'channel:\n  mode: bidirectional\n  siblings: ["reviewer", "scout-2"]\n'
    policy = parse_channel({'mode': 'bidirectional', 'siblings': ['reviewer', 'scout-2']})
    assert policy.may_message_sibling('reviewer')
    assert policy.may_message_sibling('scout-2')
    assert not policy.may_message_sibling('stranger')
    # The parent comes with the mode unless it is turned off explicitly.
    assert policy.may_message_parent()
    del extra


def test_the_parent_can_be_turned_off_while_the_siblings_stay(tmp_path):
    policy = parse_channel({'mode': 'bidirectional', 'parent': False, 'siblings': ['reviewer']})
    assert policy.bidirectional
    assert not policy.may_message_parent()
    assert policy.may_message_sibling('reviewer')


def test_sibling_names_are_normalized_and_deduplicated():
    policy = parse_channel({'mode': 'bidirectional', 'siblings': ['Reviewer', 'reviewer', ' scout ']})
    assert policy.to_siblings == ('reviewer', 'scout')


def test_neighbours_without_a_bidirectional_mode_are_an_error():
    """The short form that says "I talk back" but not "I can" is not readable."""
    with pytest.raises(AgentParseError, match='need `mode: bidirectional`'):
        parse_channel({'parent': True})


def test_an_unknown_mode_is_an_error():
    with pytest.raises(AgentParseError, match='channel.mode'):
        parse_channel('sometimes')


def test_an_unknown_key_inside_the_channel_is_an_error():
    with pytest.raises(AgentParseError, match='Unknown keys in `channel`'):
        parse_channel({'mode': 'bidirectional', 'friends': ['x']})


def test_a_sibling_name_that_is_not_kebab_case_is_an_error():
    with pytest.raises(AgentParseError, match='Invalid agent name'):
        parse_channel({'mode': 'bidirectional', 'siblings': ['Not A Name']})


def test_the_channel_describes_itself_for_the_header():
    assert ChannelPolicy().describe() == 'parent → agent'
    assert ChannelPolicy(mode=ChannelMode.BIDIRECTIONAL).describe() == 'parent ⇄ agent (parent)'
    assert (
        ChannelPolicy(mode=ChannelMode.BIDIRECTIONAL, to_siblings=('a', 'b')).describe()
        == 'parent ⇄ agent (parent, a, b)'
    )


def test_the_profile_carries_both_declarations(tmp_path):
    profile, _ = parse_profile(
        _write(tmp_path, 'max_steps: 5\nchannel:\n  mode: bidirectional\n  siblings: [reviewer]\n'),
        source='project',
    )
    assert profile.max_steps == 5
    assert profile.channel.may_message_sibling('reviewer')
    assert profile.role.selectable is True
