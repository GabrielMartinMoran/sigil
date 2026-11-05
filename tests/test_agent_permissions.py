"""The permission model: grouped rules, ordered, with resource patterns.

Four layers, in the order they fail in real use:

- **Parsing.** A profile is a file a user writes by hand, so a typo in a scope
  has to produce a message instead of a silently open agent.
- **Advertising.** A tool a rule closes is not *refused*; it never reaches the
  model. The distinction is the whole point of the design.
- **Refusing.** A rule narrowed by resource keeps the tool visible and rules on
  the call, so "the shell, but not ``rm``" still leaves a shell.
- **Matching.** Commands, paths and queries each normalize differently, and the
  rule is written by hand in every one of those three dialects.

``test_builtin_tools.py`` keeps the group table honest against the real tools;
this module tests the policy itself, with a hand-built tool set.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.engine.agents.agent import create_agent
from src.engine.agents.profile.capability import DENIED_PREFIX
from src.engine.agents.profile.models.agent_parse_error import AgentParseError
from src.engine.agents.profile.permissions import Effect, ToolPermissions, parse_permissions
from src.engine.agents.profile.registry import AgentRegistry
from src.engine.mcp.tools import register_mcp_tools
from src.engine.tooling.permission import ToolGroup, ToolPermission

FRONTMATTER = '---\nname: {name}\ndescription: hi\n{extra}---\nbody\n'


# --- parsing -----------------------------------------------------------------


def test_no_permissions_key_means_every_tool():
    permissions = parse_permissions(None)
    assert not permissions.restricted
    assert permissions.advertises('anything')
    assert permissions.permits('anything')

    assert parse_permissions({}).rules == ()
    assert not parse_permissions({}).restricted


def test_an_empty_allowlist_grants_nothing():
    permissions = parse_permissions([])
    assert permissions.restricted
    assert not permissions.advertises('read_file')
    assert permissions.describe() == 'only nothing'


def test_a_bare_list_is_an_allowlist_against_a_closed_baseline():
    permissions = parse_permissions(['read', 'ask'])
    assert permissions.advertises('read_file', (ToolGroup.READ,))
    assert permissions.advertises('ask_user', (ToolGroup.ASK,))
    assert not permissions.advertises('run')


def test_a_group_rule_covers_every_tool_of_the_group():
    permissions = parse_permissions({'read': 'deny'})
    assert not permissions.advertises('read_file', (ToolGroup.READ,))
    assert permissions.advertises('write_file', (ToolGroup.WRITE,))
    assert permissions.advertises('run', (ToolGroup.RUN,))


def test_the_last_matching_rule_wins():
    permissions = parse_permissions({'*': 'deny', 'read': 'allow'})
    assert not permissions.advertises('run')
    assert permissions.advertises('read_file', (ToolGroup.READ,))


def test_a_tool_rule_beats_the_group_it_sits_in():
    permissions = parse_permissions({'write': 'allow', 'patch_file': 'deny'})
    assert permissions.advertises('write_file', (ToolGroup.WRITE,))
    assert not permissions.advertises('patch_file', (ToolGroup.WRITE,))


def test_a_wildcard_over_names_targets_one_mcp_server():
    permissions = parse_permissions({'*': 'deny', 'mcp_github_*': 'allow'})
    assert permissions.advertises('mcp_github_search')
    assert not permissions.advertises('mcp_jira_search')


def test_a_narrowed_rule_keeps_the_tool_visible():
    permissions = parse_permissions({'run': {'allow': ['git status *']}})
    assert permissions.advertises('run', (ToolGroup.RUN,))


def test_summon_is_a_scope_and_the_subagent_name_is_its_resource():
    """`summon` is ruled on like the rest, with the profile name as the resource."""
    permissions = parse_permissions(
        {'all': 'deny', 'summon': {'allow': ['scout', 'review-*'], 'deny': ['review-secret']}}
    )
    assert permissions.advertises('summon', (ToolGroup.SUMMON,))
    assert permissions.permits('summon', (ToolGroup.SUMMON,), ('scout',))
    assert permissions.permits('summon', (ToolGroup.SUMMON,), ('review-api',))
    assert not permissions.permits('summon', (ToolGroup.SUMMON,), ('review-secret',))
    assert not permissions.permits('summon', (ToolGroup.SUMMON,), ('wizard',))


def test_summoning_everything_is_a_wildcard():
    permissions = parse_permissions({'summon': {'allow': ['*']}})
    assert permissions.permits('summon', (ToolGroup.SUMMON,), ('anything',))


def test_denying_summon_hides_the_tool_entirely():
    permissions = parse_permissions({'summon': 'deny'})
    assert not permissions.advertises('summon', (ToolGroup.SUMMON,))


def test_no_summon_rule_leaves_every_subagent_available():
    permissions = parse_permissions(None)
    assert permissions.advertises('summon', (ToolGroup.SUMMON,))
    assert permissions.permits('summon', (ToolGroup.SUMMON,), ('scout',))


def test_an_unknown_scope_is_an_error_not_an_open_agent():
    with pytest.raises(AgentParseError, match='Unknown permission scope'):
        parse_permissions({'reed': 'deny'})


def test_the_old_allow_deny_shape_is_rejected_with_the_new_one():
    with pytest.raises(AgentParseError, match='no longer accepted'):
        parse_permissions({'allow': ['read_file'], 'deny': ['run']})


def test_an_unknown_effect_is_rejected():
    with pytest.raises(AgentParseError, match='allow` or `deny'):
        parse_permissions({'read': 'maybe'})


def test_a_list_where_an_effect_belongs_is_rejected():
    with pytest.raises(AgentParseError, match='needs an effect'):
        parse_permissions({'read': ['read_file']})


def test_unknown_keys_inside_a_narrowed_rule_are_rejected():
    with pytest.raises(AgentParseError, match='Unknown keys'):
        parse_permissions({'run': {'allow': ['ls'], 'except': ['rm']}})


def test_a_permission_entry_that_is_not_a_string_is_rejected():
    with pytest.raises(AgentParseError, match='strings'):
        parse_permissions({'run': {'allow': [1, 2]}})


def test_unrestricted_is_not_the_same_as_an_empty_allowlist():
    assert not ToolPermissions().restricted
    assert ToolPermissions(rules=()).restricted is False
    assert parse_permissions([]).restricted


# --- matching ----------------------------------------------------------------


def test_a_command_pattern_ending_in_a_star_also_matches_the_bare_command():
    permissions = parse_permissions({'run': {'allow': ['git status *']}})
    assert permissions.permits('run', ('run',), ('git status',), kind='command')
    assert permissions.permits('run', ('run',), ('git status --short',), kind='command')
    assert not permissions.permits('run', ('run',), ('git stash',), kind='command')


def test_whitespace_is_collapsed_before_matching_a_command():
    permissions = parse_permissions({'run': {'allow': ['git   status *']}})
    assert permissions.permits('run', ('run',), ('  git status   --short ',), kind='command')


def test_one_denied_command_refuses_the_whole_call():
    permissions = parse_permissions({'run': {'allow': ['*'], 'deny': ['rm *']}})
    assert permissions.permits('run', ('run',), ('ls', 'cat file'), kind='command')
    assert not permissions.permits('run', ('run',), ('ls', 'rm -rf /'), kind='command')


def test_a_relative_path_pattern_matches_an_absolute_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    permissions = parse_permissions({'read': {'deny': ['*.env']}})
    assert not permissions.permits('read_file', ('read',), ('/abs/path/.env',), kind='path')
    assert permissions.permits('read_file', ('read',), ('/abs/path/notes.md',), kind='path')
    # The relative spelling has to be refused too, or the rule is decoration.
    assert not permissions.permits('read_file', ('read',), ('.env',), kind='path')


def test_a_workspace_relative_pattern_matches_the_subtree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    permissions = parse_permissions({'write': {'deny': ['secrets/*']}})
    assert not permissions.permits('write_file', ('write',), ('secrets/token.txt',), kind='path')
    assert not permissions.permits('write_file', ('write',), (str(tmp_path / 'secrets' / 'a.txt'),), kind='path')
    assert permissions.permits('write_file', ('write',), ('src/app.py',), kind='path')


def test_a_home_pattern_is_expanded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    permissions = parse_permissions({'read': {'allow': ['$HOME/reference/*']}})
    assert permissions.permits('read_file', ('read',), (str(tmp_path / 'home' / 'reference' / 'a.md'),), kind='path')
    assert not permissions.permits('read_file', ('read',), (str(tmp_path / 'home' / 'other' / 'a.md'),), kind='path')


def test_a_string_pattern_is_one_pattern_and_not_a_comma_list():
    """A command can contain a comma, so a bare string is a single rule."""
    permissions = parse_permissions({'run': {'allow': 'git log --pretty=%s,%d'}})
    assert permissions.permits('run', ('run',), ('git log --pretty=%s,%d',), kind='command')
    assert not permissions.permits('run', ('run',), ('git log --pretty=%s',), kind='command')


def test_a_query_is_matched_as_given():
    permissions = parse_permissions({'web': {'deny': ['*porn*']}})
    assert not permissions.permits('web_search', ('web',), ('free porn',), kind='text')
    assert permissions.permits('web_search', ('web',), ('python asyncio',), kind='text')


def test_a_rule_without_patterns_speaks_about_every_resource():
    permissions = parse_permissions({'read': 'deny'})
    assert not permissions.permits('read_file', ('read',), ('anything.txt',), kind='path')


def test_a_scope_the_call_does_not_belong_to_does_not_decide_it():
    permissions = parse_permissions({'write': 'deny'})
    assert permissions.permits('read_file', ('read',), ('a.txt',), kind='path')


def test_an_unmatched_tool_is_allowed():
    """A profile that lists what it closes cannot accidentally close the rest."""
    permissions = parse_permissions({'run': 'deny'})
    assert permissions.advertises('read_file', ('read',))
    assert permissions.advertises('mcp_github_search')


# --- the picker --------------------------------------------------------------


def test_describe_says_what_is_closed():
    assert parse_permissions({'write': 'deny'}).describe() == 'all tools except write'


def test_describe_lists_the_only_scope_from_a_closed_baseline():
    assert parse_permissions({'*': 'deny', 'read': 'allow'}).describe() == 'only read'


def test_describe_mentions_the_narrowed_scopes():
    described = parse_permissions({'run': {'allow': ['git status *']}}).describe()
    assert 'run by pattern' in described


# --- the capability, end to end ----------------------------------------------


def _write(root: Path, name: str, *, extra: str = '') -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f'{name}.md').write_text(FRONTMATTER.format(name=name, extra=extra), encoding='utf-8')


def _registry(tmp_path: Path, *, extra: str = '', active: str = 'limited') -> AgentRegistry:
    _write(tmp_path, 'limited', extra=extra)
    return AgentRegistry([('project', tmp_path)], active=active)


def _seen_tools(agent: Agent) -> list[str]:
    """The tool names the model was actually offered, in one turn."""
    seen: list[list[str]] = []

    def model_function(_messages, info: AgentInfo):
        seen.append([tool.name for tool in info.function_tools])
        return ModelResponse(parts=[TextPart('ok')])

    with agent.override(model=FunctionModel(model_function)):
        asyncio.run(agent.run('hello'))
    return seen[-1]


def _agent_with_tools(registry: AgentRegistry | None) -> Agent:
    agent = create_agent(None, agent_registry=registry)

    @agent.tool_plain(metadata=ToolPermission(groups=(ToolGroup.READ,), resource='path', kind='path').metadata())
    def read_file(path: str = 'a.txt') -> str:
        """Read."""
        return 'r'

    @agent.tool_plain(metadata=ToolPermission(groups=(ToolGroup.RUN,), resource='cmds', kind='command').metadata())
    def run(cmds: list[str] | None = None) -> str:
        """Run."""
        return 's'

    @agent.tool_plain
    def mcp_github_search() -> str:
        """MCP tool."""
        return 'm'

    return agent


def test_a_closed_group_is_never_advertised(tmp_path):
    registry = _registry(tmp_path, extra='permissions:\n  write: deny\n')
    assert set(_seen_tools(_agent_with_tools(registry))) == {'read_file', 'run', 'mcp_github_search'}


def test_an_allowlist_leaves_only_the_granted_group(tmp_path):
    registry = _registry(tmp_path, extra='permissions: [read]\n')
    assert _seen_tools(_agent_with_tools(registry)) == ['read_file']


def test_a_denied_tool_cannot_be_called(tmp_path):
    """The model cannot call what it was never offered, even if it insists."""
    registry = _registry(tmp_path, extra='permissions:\n  run: deny\n')
    agent = _agent_with_tools(registry)

    def model_function(messages, _info: AgentInfo):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart('run', {'cmds': ['ls']})])
        return ModelResponse(parts=[TextPart('done')])

    with agent.override(model=FunctionModel(model_function)):
        result = asyncio.run(agent.run('go'))

    # The refusal is the framework's "unknown tool": there is nothing to check,
    # the tool does not exist in this request.
    text = ' '.join(str(message) for message in result.all_messages())
    assert 'run' in text
    assert 'Unknown tool' in text


def test_a_narrowed_tool_refuses_the_calls_outside_its_patterns(tmp_path):
    registry = _registry(tmp_path, extra='permissions:\n  run:\n    allow: ["git status *"]\n')
    agent = _agent_with_tools(registry)

    def model_function(messages, _info: AgentInfo):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart('run', {'cmds': ['rm -rf /']})])
        return ModelResponse(parts=[TextPart('done')])

    with agent.override(model=FunctionModel(model_function)):
        result = asyncio.run(agent.run('go'))

    outcomes = [
        part for message in result.all_messages() for part in getattr(message, 'parts', ()) if hasattr(part, 'outcome')
    ]
    assert [part.outcome for part in outcomes] == ['failed']
    assert DENIED_PREFIX in str(outcomes[0].content)


def test_a_narrowed_tool_still_runs_the_calls_inside_its_patterns(tmp_path):
    registry = _registry(tmp_path, extra='permissions:\n  run:\n    allow: ["git status *"]\n')
    agent = _agent_with_tools(registry)

    def model_function(messages, _info: AgentInfo):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart('run', {'cmds': ['git status']})])
        return ModelResponse(parts=[TextPart('done')])

    with agent.override(model=FunctionModel(model_function)):
        result = asyncio.run(agent.run('go'))

    outcomes = [
        part for message in result.all_messages() for part in getattr(message, 'parts', ()) if hasattr(part, 'outcome')
    ]
    assert [part.outcome for part in outcomes] == ['success']


def test_without_a_registry_nothing_is_filtered():
    assert set(_seen_tools(_agent_with_tools(None))) == {'read_file', 'run', 'mcp_github_search'}


def test_switching_agent_changes_the_tool_set_mid_session(tmp_path):
    _write(tmp_path, 'all')
    _write(tmp_path, 'readonly', extra='permissions: [read]\n')
    registry = AgentRegistry([('project', tmp_path)], active='all')
    agent = _agent_with_tools(registry)

    wide = _seen_tools(agent)
    registry.activate('readonly')
    narrow = _seen_tools(agent)

    assert 'run' in wide
    assert 'run' not in narrow
    assert narrow == ['read_file']


def test_a_rule_narrowed_by_a_group_the_tool_declares_still_applies(tmp_path):
    """A tool in two groups — a skill's script runs — is closed by either."""
    registry = _registry(tmp_path, extra='permissions:\n  run: deny\n')
    agent = create_agent(None, agent_registry=registry)

    @agent.tool_plain(metadata=ToolPermission(groups=(ToolGroup.SKILLS, ToolGroup.RUN)).metadata())
    def run_skill_script() -> str:
        """Run a bundled script."""
        return 's'

    assert _seen_tools(agent) == []


def test_a_permission_filter_never_touches_an_unrestricted_profile(tmp_path):
    registry = _registry(tmp_path)
    permissions = registry.active.permissions
    assert not permissions.restricted
    assert permissions.advertises('run', (ToolGroup.RUN,))
    assert permissions.permits('run', (ToolGroup.RUN,), ('rm -rf /',))


def test_the_rules_are_kept_in_the_order_they_were_written():
    permissions = parse_permissions({'*': 'deny', 'read': 'allow', 'read_file': 'deny'})
    assert [rule.scope for rule in permissions.rules] == ['*', 'read', 'read_file']
    assert [rule.effect for rule in permissions.rules] == [Effect.DENY, Effect.ALLOW, Effect.DENY]


def test_the_picker_line_names_the_scope_and_not_the_rule_list(tmp_path):
    extra = 'permissions:\n  all: deny\n  read: allow\n  run:\n    allow: ["git status *"]\n'
    profile = _registry(tmp_path, extra=extra).active
    assert profile is not None
    assert profile.permissions.describe() == 'only read (run by pattern)'


def test_a_call_with_an_unknown_declaration_is_matched_by_name_only():
    permissions = parse_permissions({'run': 'deny'})
    assert not permissions.advertises('run')
    assert permissions.advertises('read_file')


def test_permissions_survive_a_round_trip_through_the_profile_file(tmp_path):
    profile = _registry(tmp_path, extra='permissions:\n  write: deny\n').active
    assert profile is not None
    assert profile.permissions.describe() == 'all tools except write'
    assert isinstance(profile.permissions.rules, tuple)
    assert profile.permissions.rules[0].scope == 'write'


def test_a_tool_declaration_reads_back_as_written():
    from src.engine.tooling.permission import permission_of

    declaration = ToolPermission(groups=(ToolGroup.READ,), resource='path', kind='path')
    tool_def: Any = type('ToolDef', (), {'metadata': declaration.metadata()})()
    parsed = permission_of(tool_def)
    assert parsed == declaration


def test_a_narrowed_server_name_refuses_only_that_server(tmp_path):
    """The MCP meta-tools name their server, so a rule can keep the others."""
    registry = _registry(tmp_path, extra='permissions:\n  mcp:\n    deny: [github]\n')
    agent = create_agent(None, agent_registry=registry)
    register_mcp_tools(agent, _FakeManager())

    def model_function(messages, _info: AgentInfo):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart('read_mcp_resource', {'server': 'github', 'uri': 'x'})])
        return ModelResponse(parts=[TextPart('done')])

    with agent.override(model=FunctionModel(model_function)):
        refused = asyncio.run(agent.run('go'))

    def model_function_allowed(messages, _info: AgentInfo):
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart('read_mcp_resource', {'server': 'jira', 'uri': 'x'})])
        return ModelResponse(parts=[TextPart('done')])

    with agent.override(model=FunctionModel(model_function_allowed)):
        allowed = asyncio.run(agent.run('go'))

    assert _outcomes(refused) == ['failed']
    assert _outcomes(allowed) == ['success']


class _FakeManager:
    """Enough of ``MCPManager`` for the meta-tools to register and be called."""

    def __bool__(self) -> bool:
        return True

    async def describe(self, *, connect: bool = False) -> list[Any]:
        del connect
        return []

    async def list_resources(self, server: str | None = None) -> list[Any]:
        del server
        return []

    async def read_resource(self, server: str, uri: str) -> Any:
        del server, uri
        return 'content'

    async def list_prompts(self, server: str | None = None) -> list[Any]:
        del server
        return []

    async def get_prompt(self, server: str, name: str, arguments: Any = None) -> Any:
        del server, name, arguments
        return 'prompt'


def _outcomes(result: Any) -> list[str]:
    return [
        part.outcome
        for message in result.all_messages()
        for part in getattr(message, 'parts', ())
        if hasattr(part, 'outcome')
    ]
