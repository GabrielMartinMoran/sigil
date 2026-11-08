"""The Pulsar Agent Squad profiles, as they are installed in this project.

The squad is a `.sigil/agents` set, not a built-in: it is the first real user of the
tree, and it exercises everything the tree was built for at once — roles, an
allow-listed `summon`, per-profile step budgets, the channel between two siblings,
and ids short enough to be read in a prompt.

Two layers are pinned here:

- **The declarations.** Permissions, budgets and channels, checked against the real
  parser and the real matcher, because a profile is a file a user edits and the
  failure mode is a silent widening.
- **The round trip.** One scripted delegation through a real `Agent.iter`, with the
  lead dispatching the pair and the pair talking to each other through the channel.
  The models are fake; nothing else is.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import UsageLimits

from src.engine.agents.agent import create_agent
from src.engine.agents.factory import AgentFactory, AgentSpec
from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.profile.registry import AgentRegistry
from src.engine.agents.profile.source import ProfileSource
from src.engine.agents.tree.rendering import render_notifications, render_peers, summonable
from src.engine.agents.tree.store import AgentTreeStore
from src.engine.agents.tree.summoner import SigilDeps, SubagentSummoner
from src.engine.agents.tree.tools import register_tree_tools
from src.engine.files.tools import register_file_tools
from src.engine.sessions.session_store import SessionStore
from src.engine.shell.tools import register_shell_tools
from src.engine.tooling.permission import ResourceKind, ToolGroup

PROJECT = Path(__file__).resolve().parent.parent
AGENTS = PROJECT / '.sigil' / 'agents'
BUILTIN = PROJECT / 'src' / 'engine' / 'agents' / 'profile' / 'builtin'

SUMMON = (ToolGroup.SUMMON,)
WRITE = (ToolGroup.WRITE,)
READ = (ToolGroup.READ,)
RUN = (ToolGroup.RUN,)


@pytest.fixture(scope='module')
def registry() -> AgentRegistry:
    return AgentRegistry([('project', AGENTS), ('builtin', BUILTIN)], active=None)


@pytest.fixture(scope='module')
def squad(registry: AgentRegistry):
    return (
        registry.require('pulsar-lead'),
        registry.require('pulsar-researcher'),
        registry.require('pulsar-engineer'),
        registry.require('pulsar-copilot'),
    )


# --- the declarations --------------------------------------------------------


def test_the_squad_is_installed_as_a_project_set(registry: AgentRegistry):
    assert registry.errors == []
    # The Lead is the only one the picker offers: the workers are summoned.
    assert 'pulsar-lead' in [profile.name for profile in registry.agents()]
    assert {profile.name for profile in registry.subagents()} >= {
        'pulsar-researcher',
        'pulsar-engineer',
        'pulsar-copilot',
    }
    assert registry.get('pulsar-lead').role is AgentRole.AGENT
    assert registry.get('pulsar-engineer').role is AgentRole.SUBAGENT


def test_the_workers_declare_the_step_budgets_of_the_original_squad(squad):
    lead, researcher, engineer, copilot = squad
    assert (engineer.max_steps, copilot.max_steps) == (180, 160)
    # The Lead and the researcher are bounded by the session, not by a slice.
    assert lead.max_steps is None
    assert researcher.max_steps is None


def test_the_lead_can_only_summon_its_own_workers(squad, registry: AgentRegistry):
    lead = squad[0]
    names = [profile.name for profile in summonable(registry, lead)]
    assert names == ['pulsar-copilot', 'pulsar-engineer', 'pulsar-researcher']
    # A builtin subagent is not part of the squad, and the allow list is exhaustive.
    assert lead.permissions.permits('summon', SUMMON, ('scout',)) is False


def test_the_lead_never_writes_source(squad):
    lead = squad[0]
    assert lead.permissions.permits('write_file', WRITE, ('src/sigil.py',), ResourceKind.PATH) is False
    assert lead.permissions.permits('patch_file', WRITE, ('tests/test_x.py',), ResourceKind.PATH) is False
    # ...but it owns the plan and the specs.
    assert lead.permissions.permits('write_file', WRITE, ('docs/plans/unit.md',), ResourceKind.PATH) is True
    assert lead.permissions.permits('write_file', WRITE, ('specs/features/a.feature',), ResourceKind.PATH) is True
    # A shell it cannot use for tests: adjudication is reading, not running.
    assert lead.permissions.permits('run', RUN, ('pytest -q',), ResourceKind.COMMAND) is False
    assert lead.permissions.permits('run', RUN, ('git status',), ResourceKind.COMMAND) is True


def test_the_engineer_writes_code_but_not_the_lead_artifacts(squad):
    engineer = squad[2]
    assert engineer.permissions.permits('write_file', WRITE, ('src/x.py',), ResourceKind.PATH) is True
    assert engineer.permissions.permits('patch_file', WRITE, ('tests/test_x.py',), ResourceKind.PATH) is True
    assert engineer.permissions.permits('write_file', WRITE, ('specs/a.feature',), ResourceKind.PATH) is False
    assert engineer.permissions.permits('patch_file', WRITE, ('docs/plans/unit.md',), ResourceKind.PATH) is False
    assert engineer.permissions.permits('run', RUN, ('pytest -q',), ResourceKind.COMMAND) is True
    # The driver cannot delegate, and it cannot audit itself in place of the peer.
    assert engineer.permissions.advertises('summon', SUMMON) is False


def test_the_copilot_is_read_only_with_a_shell(squad):
    copilot = squad[3]
    assert copilot.permissions.permits('write_file', WRITE, ('src/x.py',), ResourceKind.PATH) is False
    assert copilot.permissions.permits('patch_file', WRITE, ('src/x.py',), ResourceKind.PATH) is False
    # The shell is the point: it re-executes what the driver claims.
    assert copilot.permissions.permits('run', RUN, ('pytest -q',), ResourceKind.COMMAND) is True
    assert copilot.permissions.advertises('summon', SUMMON) is False


def test_the_researcher_cannot_write_or_run(squad):
    researcher = squad[1]
    assert researcher.permissions.advertises('write_file', WRITE) is False
    assert researcher.permissions.advertises('run', RUN) is False
    assert researcher.permissions.permits('read_file', READ, ('src/x.py',), ResourceKind.PATH) is True
    assert researcher.permissions.advertises('web_search', (ToolGroup.WEB,)) is True


def test_only_the_pair_has_a_channel_and_only_to_each_other(squad):
    lead, researcher, engineer, copilot = squad
    assert not lead.channel.bidirectional
    assert not researcher.channel.bidirectional
    assert engineer.channel.bidirectional and copilot.channel.bidirectional
    assert engineer.channel.may_message_sibling('pulsar-copilot')
    assert copilot.channel.may_message_sibling('pulsar-engineer')
    assert not engineer.channel.may_message_sibling('pulsar-researcher')
    # Both can reach the Lead: a unit that cannot be closed is its decision.
    assert engineer.channel.may_message_parent()
    assert copilot.channel.may_message_parent()


def test_the_pair_sees_each_other_in_the_prompt_with_a_usable_id(tmp_path: Path):
    """The ids the block shows are the ones the tools take, short form included."""
    registry = AgentRegistry([('project', AGENTS), ('builtin', BUILTIN)], active='pulsar-lead')
    engineer = registry.require('pulsar-engineer')
    trees = AgentTreeStore()
    session = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions').create_session()
    tree = trees.bind(session, root_name='pulsar-lead')
    driver = tree.add(parent_id=tree.root_id, name='pulsar-engineer', role=AgentRole.SUBAGENT, task='unit 1')
    peer = tree.add(parent_id=tree.root_id, name='pulsar-copilot', role=AgentRole.SUBAGENT, task='unit 1')
    summoner = SubagentSummoner(trees=trees, registry=registry, builder=lambda: None)

    block = render_peers(tree, driver.id, profile=engineer, is_live=summoner.is_live)

    assert '## The agents around you' in block
    assert f'`{peer.short_id}` · **pulsar-copilot**' in block
    assert f'`{tree.root.short_id}` · **pulsar-lead**' in block
    # The researcher is a sibling too, but the channel does not name it.
    assert 'pulsar-researcher' not in block

    # And the short id really resolves to that execution, with or without the hash.
    assert summoner.resolve(peer.short_id) is peer
    assert summoner.resolve(f'#{peer.short_id}') is peer


# --- the round trip ----------------------------------------------------------

IMPLEMENTATION_REPORT = (
    '<implementation_report>\n'
    '  <authorization_check>VALID</authorization_check>\n'
    '  <verdict>PASS</verdict>\n'
    '  <scenario_results>@unit — PASS — tests/test_unit.py</scenario_results>\n'
    '  <full_suite_status>DEFERRED_TO_PEER</full_suite_status>\n'
    '  <files_changed>src/unit.py, tests/test_unit.py</files_changed>\n'
    '  <pair_outcome>approved</pair_outcome>\n'
    '</implementation_report>'
)

AUDIT_REPORT = (
    '<audit_report>\n'
    '  <verdict>PASS</verdict>\n'
    '  <authorization_check>VALID</authorization_check>\n'
    '  <full_suite_audit><full_suite_status>PASS</full_suite_status>'
    '<command>pytest -q</command><exit_code>0</exit_code></full_suite_audit>\n'
    '  <scope_compliance><files_changed>src/unit.py, tests/test_unit.py</files_changed></scope_compliance>\n'
    '  <recommendation>APPROVE</recommendation>\n'
    '</audit_report>'
)


def prompts_of(messages: list[ModelMessage]) -> list[str]:
    return [
        str(part.content)
        for message in messages
        for part in getattr(message, 'parts', ())
        if getattr(part, 'part_kind', '') == 'user-prompt'
    ]


def returns_of(messages: list[ModelMessage]) -> list[str]:
    return [
        str(getattr(part, 'content', ''))
        for message in messages
        for part in getattr(message, 'parts', ())
        if getattr(part, 'part_kind', '') == 'tool-return'
    ]


class Script:
    """The four models of one delegation, with the state each one needs.

    Every script is a function of the history, which is what a model is: no sleeps
    between workers, no shared clock. The one bounded wait is inside a worker that
    is waiting for a peer message, and it exists because a subagent that stops making
    tool calls ends its run.
    """

    def __init__(self) -> None:
        self.log: list[str] = []
        self.engineer_sent = False
        self.engineer_reported = False
        self.copilot_approved = False
        self.copilot_reported = False
        self.waits = 0

    # --- the lead ---

    def lead(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen = '\n'.join(prompts_of(messages))
        if 'implementation_report' in seen and 'audit_report' in seen:
            self.log.append('lead:adjudicate')
            return ModelResponse(parts=[TextPart('Pair delivered: PASS / PASS.')])
        if not returns_of(messages):
            self.log.append('lead:dispatch')
            return ModelResponse(
                parts=[
                    ToolCallPart('summon', {'agent': 'pulsar-copilot', 'task': 'audit unit 1', 'mode': 'async'}),
                    ToolCallPart('summon', {'agent': 'pulsar-engineer', 'task': 'implement unit 1', 'mode': 'async'}),
                ]
            )
        self.log.append('lead:end-turn')
        return ModelResponse(parts=[TextPart('Dispatched the pair.')])

    # --- the driver ---

    def engineer(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen = '\n'.join([*prompts_of(messages), info.instructions or ''])
        results = returns_of(messages)

        if 'approved unit 1' in seen and not self.engineer_reported:
            self.engineer_reported = True
            self.log.append('engineer:report')
            return ModelResponse(parts=[TextPart(IMPLEMENTATION_REPORT)])

        if not self.engineer_sent:
            table = next((text for text in results if 'agent | state' in text), '')
            found = re.findall(r'^([0-9a-f]{8}) \| pulsar-copilot', table, re.MULTILINE)
            if not found:
                self.log.append('engineer:list')
                return ModelResponse(parts=[ToolCallPart('list_agents', {})])
            self.engineer_sent = True
            self.log.append('engineer:ready_for_review')
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        'send_message',
                        {'agent_id': found[0], 'message': 'ready_for_review unit 1 — exit 0 — 3 passed'},
                    )
                ]
            )

        self.waits += 1
        self.log.append('engineer:wait')
        return ModelResponse(parts=[ToolCallPart('run', {'cmds': ['sleep 0.05']})])

    # --- the peer ---

    def copilot(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen = '\n'.join([*prompts_of(messages), info.instructions or ''])
        results = returns_of(messages)

        if 'ready_for_review' in seen and not self.copilot_approved:
            table = '\n'.join(results)
            found = re.findall(r'^([0-9a-f]{8}) \| pulsar-engineer', table, re.MULTILINE)
            if not found:
                self.log.append('copilot:list')
                return ModelResponse(parts=[ToolCallPart('list_agents', {})])
            self.copilot_approved = True
            self.log.append('copilot:approved')
            return ModelResponse(
                parts=[
                    ToolCallPart('run', {'cmds': ['echo auditing']}),
                    ToolCallPart(
                        'send_message',
                        {'agent_id': found[0], 'message': 'approved unit 1 — exit 0 — 3 passed'},
                    ),
                ]
            )

        if self.copilot_approved and not self.copilot_reported:
            self.copilot_reported = True
            self.log.append('copilot:report')
            return ModelResponse(parts=[TextPart(AUDIT_REPORT)])

        if not results:
            self.log.append('copilot:list')
            return ModelResponse(parts=[ToolCallPart('list_agents', {})])
        self.waits += 1
        self.log.append('copilot:wait')
        return ModelResponse(parts=[ToolCallPart('run', {'cmds': ['sleep 0.05']})])


@pytest.fixture()
def squad_env(tmp_path: Path, registry: AgentRegistry):
    """The squad wired like the app wires it: same tools, same blocks, one tree."""
    trees = AgentTreeStore()
    script = Script()
    samples = {
        'pulsar-lead': script.lead,
        'pulsar-engineer': script.engineer,
        'pulsar-copilot': script.copilot,
    }

    def node_blocks(node_id: str | None) -> str:
        tree = trees.active
        node = tree.node(node_id)
        profile = registry.get(node.name) if node is not None else registry.active
        return render_peers(tree, node_id, profile=profile, is_live=summoner.is_live) + render_notifications(
            tree, node_id
        )

    def tools(target) -> None:
        register_shell_tools(target)
        register_file_tools(target)
        register_tree_tools(target, summoner)

    def build(profile):
        agent = create_agent(
            None,
            model=FunctionModel(samples[profile.name]),
            agent_registry=registry,
            profile=ProfileSource.fixed(profile),
            node_blocks=node_blocks,
        )
        tools(agent)
        return agent

    summoner = SubagentSummoner(trees=trees, registry=registry, builder=lambda: build, max_steps=40)
    factory = AgentFactory(
        AgentSpec(model=FunctionModel(script.lead), agent_registry=registry, tool_registrars=[tools])
    )
    session = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions').create_session()
    trees.bind(session, root_name='pulsar-lead')
    return script, trees, summoner, factory


def test_the_pair_delivers_a_delegation_through_the_channel(squad_env):
    """Lead dispatches, driver and peer talk, both report, the Lead adjudicates."""
    script, trees, summoner, factory = squad_env

    async def inner():
        lead = factory.root()
        async with lead.iter('implement unit 1', deps=SigilDeps(), usage_limits=UsageLimits(request_limit=None)) as run:
            async for _node in run:
                pass
        first = run.result.output if run.result else ''

        for _ in range(400):
            await asyncio.sleep(0.02)
            nodes = trees.active.nodes()
            if len(nodes) == 3 and all(node.status.finished for node in nodes[1:]):
                break

        pending = summoner.take_messages()
        second = ''
        if pending:
            lead = factory.root()
            prompt = '\n\n'.join([*pending, 'The pair reported.'])
            async with lead.iter(prompt, deps=SigilDeps(), usage_limits=UsageLimits(request_limit=None)) as again:
                async for _node in again:
                    pass
            second = again.result.output if again.result else ''
        return first, pending, second

    first, pending, second = asyncio.run(inner())
    nodes = trees.active.nodes()
    engineer = next(node for node in nodes if node.name == 'pulsar-engineer')
    copilot = next(node for node in nodes if node.name == 'pulsar-copilot')

    assert first == 'Dispatched the pair.'
    assert engineer.status.finished and copilot.status.finished
    assert 'implementation_report' in (engineer.result or '')
    assert 'audit_report' in (copilot.result or '')

    # Notices travel through dynamic context, never as user instructions.
    driver_channel = [entry for entry in trees.active.notifications.for_receiver(engineer.id)]
    peer_channel = [entry for entry in trees.active.notifications.for_receiver(copilot.id)]
    assert len(driver_channel) == 1 and 'approved unit 1' in driver_channel[0].text
    assert len(peer_channel) == 1 and 'ready_for_review' in peer_channel[0].text
    assert not any('approved unit 1' in text for text in prompts_of(engineer.messages))
    assert not any('ready_for_review' in text for text in prompts_of(copilot.messages))
    assert any('approved unit 1' in (getattr(message, 'instructions', '') or '') for message in engineer.messages)
    assert any('ready_for_review' in (getattr(message, 'instructions', '') or '') for message in copilot.messages)

    # Both async results waited for the next turn, and the Lead adjudicated them.
    assert len(pending) == 2
    assert second == 'Pair delivered: PASS / PASS.'
    assert {'lead:dispatch', 'engineer:ready_for_review', 'copilot:approved'} <= set(script.log)
