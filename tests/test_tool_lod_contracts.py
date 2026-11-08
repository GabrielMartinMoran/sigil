"""Real tool models have monotonic, loss-aware input and output projections."""

from __future__ import annotations

import json

import pytest
from pydantic_ai.messages import BinaryContent, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart

from src.engine.context.compaction.coordinator import apply, exceptions, plan
from src.engine.context.compaction.policy import CompactionPolicy
from src.engine.files.models.file_content import FileContent
from src.engine.files.models.patch_result import PatchResult
from src.engine.files.models.write_result import WriteResult
from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_result import QuestionResult
from src.engine.mcp.tool_models.prompt_info import PromptInfo
from src.engine.mcp.tool_models.prompt_message_info import PromptMessageInfo
from src.engine.mcp.tool_models.prompt_result_info import PromptResultInfo
from src.engine.mcp.tool_models.resource_content import ResourceContent
from src.engine.mcp.tool_models.resource_info import ResourceInfo
from src.engine.mcp.tool_models.server_status import ServerStatus
from src.engine.roadmap.tools import RoadmapSnapshot
from src.engine.shell.models.run_tool_output import RunToolOutput
from src.engine.skills.tool_models.loaded_skill import LoadedSkill
from src.engine.skills.tool_models.resource_content import ResourceContent as SkillResourceContent
from src.engine.skills.tool_models.script_run_result import ScriptRunResult
from src.engine.skills.tool_models.skill_summary import SkillSummary
from src.engine.tooling.lod import GENERIC_LOD, ToolLOD, compact_json, encoded, normalize, shorten
from src.engine.tooling.lod_policies import ALL_POLICIES
from src.engine.web.models.page_content import PageContent
from src.engine.web.models.search_response import SearchResponse
from src.engine.web.models.search_result import SearchResult

LONG = 'payload ' * 2000


def samples() -> dict:
    return {
        'run': [RunToolOutput(returncode=1, stdout=LONG, stderr='failure')],
        'read_file': FileContent(
            path='file.py', size=1000, truncated=True, binary=False, encoding='utf-8', content=LONG
        ),
        'write_file': WriteResult(path='file.py', bytes_written=100, created=True, appended=False),
        'patch_file': PatchResult(
            path='file.py', mode='string', replacements=1, bytes_before=1, bytes_after=2, preview=LONG
        ),
        'web_search': SearchResponse(
            query='query', count=1, results=[SearchResult(title='result', url='https://test', snippet=LONG)]
        ),
        'read_url': PageContent(url='https://test', format='text', content=LONG),
        'list_skills': [SkillSummary(name='demo', description=LONG, source='project', path='/skills/demo')],
        'load_skill': LoadedSkill(
            name='demo', description=LONG, source='project', path='/skills/demo', resources=[], note='loaded'
        ),
        'read_skill_resource': SkillResourceContent(
            skill='demo', path='ref.md', size=1000, truncated=True, binary=False, note='reference'
        ),
        'run_skill_script': ScriptRunResult(skill='demo', script='run.py', returncode=1, stderr=LONG),
        'ask_user': QuestionResult(answers=[QuestionAnswer(question='Deploy?', answer='Do not deploy')]),
        'update_roadmap': RoadmapSnapshot(items=[], counts={}, persistence_error='disk full'),
        'view_image': 'image img_abcdefgh (image dimensions and cost)',
        'summon': '#abc123 worker stopped\n' + LONG,
        'send_message': 'Notification notice123 delivered to worker #abc123; pending ACK.',
        'ack_notifications': 'ACK recorded for notice123. Receipt only; notifications remain visible.',
        'kill_subagent': '#abc123 worker stopped, along with everything it had summoned.',
        'list_agents': 'id | agent | state | mode | who summoned it | task\nabc123 | worker | failed | sync | root | '
        + LONG,
        'list_mcp_servers': [
            ServerStatus(name='local', transport='stdio', enabled=False, prefix='local', error=LONG, source='config')
        ],
        'list_mcp_resources': [ResourceInfo(server='local', uri='resource:test', description=LONG)],
        'read_mcp_resource': ResourceContent(server='local', uri='resource:test', content=LONG),
        'list_mcp_prompts': [PromptInfo(server='local', name='prompt', description=LONG)],
        'get_mcp_prompt': PromptResultInfo(
            server='local', name='prompt', messages=[PromptMessageInfo(role='user', content=LONG)]
        ),
    }


@pytest.mark.parametrize('name', sorted(ALL_POLICIES))
def test_every_registered_policy_handles_real_output_and_stored_json_monotonically(name):
    value = samples()[name]
    policy = ALL_POLICIES[name]
    stored = normalize(value)
    sizes = []
    for level in ToolLOD:
        live = policy.compact_output(value, level)
        replay = policy.compact_output(stored, level)
        assert encoded(live) == encoded(replay)
        assert encoded(policy.compact_output(live, level)) == encoded(live)
        sizes.append(len(encoded(live)))
    assert sizes == sorted(sizes, reverse=True)
    assert encoded(value) == encoded(stored)


@pytest.mark.parametrize('name', sorted(ALL_POLICIES))
def test_every_policy_handles_its_arguments_at_all_levels(name):
    args = {
        'cmds': [LONG],
        'path': 'file.py',
        'content': LONG,
        'new_string': LONG,
        'name': 'demo',
        'script': 'run.py',
        'args': [LONG],
        'query': LONG,
        'url': 'https://test',
        'server': 'local',
        'uri': 'resource:test',
        'action': 'add',
        'items': [{'id': 'todo', 'title': LONG, 'status': 'pending'}],
        'questions': [{'question': LONG, 'options': [{'label': LONG}]}],
        'source': 'img_abcdefgh',
        'task': LONG,
        'message': LONG,
        'agent_id': 'abc123',
        'notification_ids': ['notice123'],
        'arguments': {'topic': LONG},
        'max_bytes': 100,
    }
    policy = ALL_POLICIES[name]
    sizes = [len(encoded(policy.compact_input(args, level))) for level in ToolLOD]
    assert sizes == sorted(sizes, reverse=True)
    for level in ToolLOD:
        assert isinstance(policy.compact_input(args, level), dict)


@pytest.mark.parametrize('level', [ToolLOD.LOD_1, ToolLOD.LOD_2, ToolLOD.LOD_3])
def test_generic_json_preserves_validity_and_aggregate_budget_with_escaping(level):
    value = {'rows': ['"\\\n' * 1000 for _ in range(20)]}
    for original in (value, '"\\\n' * 2000):
        result = compact_json(original, level)
        assert json.loads(result) is not None
        assert len(result) <= {1: 2000, 2: 500, 3: 100}[level]


def test_failed_structured_tool_returns_are_not_interpreted_as_success_models():
    messages = [
        ModelResponse(parts=[ToolCallPart(tool_name='web_search', args={'query': 'q'}, tool_call_id='c')]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name='web_search',
                    content='SearchError: unavailable ' + LONG,
                    outcome='failed',
                    tool_call_id='c',
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content='ok')]),
    ]
    policy = CompactionPolicy(lod_ages=(0, 1, 2))
    planned = plan(messages, policy=policy, pressure=True, lookup=ALL_POLICIES.get)
    reduced, _ = apply(messages, planned, lookup=ALL_POLICIES.get)
    result = reduced[1].parts[0]
    assert result.outcome == 'failed'
    assert 'SearchError' in result.content
    assert result.tool_call_id == 'c'


def test_opaque_external_binary_is_preserved_and_reported():
    binary = BinaryContent(data=b'\x00' * 5000, media_type='application/octet-stream')
    messages = [
        ModelResponse(parts=[ToolCallPart(tool_name='foreign', args={'q': LONG}, tool_call_id='c')]),
        ModelRequest(parts=[ToolReturnPart(tool_name='foreign', content=[binary], tool_call_id='c')]),
        ModelResponse(parts=[TextPart(content='ok')]),
    ]
    planned = plan(messages, policy=CompactionPolicy(lod_ages=(0, 1, 2)), pressure=True)
    reduced, _ = apply(messages, planned)
    assert reduced[1].parts[0].content == [binary]
    assert 'opaque_binary_or_multimodal' in exceptions(messages, planned, None)


def test_stream_truncation_flags_and_exact_omitted_count_are_honest():
    result = ALL_POLICIES['run'].compact_output([RunToolOutput(returncode=1, stdout=LONG)], ToolLOD.LOD_3)[0]
    assert result['stdout_truncated'] is True
    shortened = shorten('a' * 1000, 100)
    prefix, notice = shortened.split('\n\n', 1)
    assert f'{1000 - len(prefix):,}' in notice
    assert len(shortened) <= 100


def test_user_decisions_and_identity_are_not_cut_to_hit_own_semantic_sizes():
    value = QuestionResult(answers=[QuestionAnswer(question='Confirm?', answer='Do not ' + LONG)])
    assert ALL_POLICIES['ask_user'].compact_output(value, ToolLOD.LOD_3)['answers'][0]['answer'] == 'Do not ' + LONG
    source = 'https://test/' + 'long/path/' * 100
    assert ALL_POLICIES['view_image'].compact_input({'source': source}, ToolLOD.LOD_3)['source'] == source


def test_generic_nested_failure_metadata_survives_even_when_it_exceeds_the_target():
    value = {'rows': ['x' * 10000], 'envelope': {'error': 'nested failure ' * 100, 'status': 'failed'}}
    reduced = GENERIC_LOD.compact_output(value, ToolLOD.LOD_3)
    assert reduced['metadata']['envelope']['error'] == value['envelope']['error']
    assert reduced['metadata']['envelope']['status'] == 'failed'


def test_generic_policy_accepts_json_schema_type_arrays_without_treating_them_as_binary():
    value = {'type': ['string', 'null'], 'examples': ['x' * 5000]}
    reduced = GENERIC_LOD.compact_output(value, ToolLOD.LOD_3)
    assert reduced['truncated'] is True
    assert len(json.dumps(reduced)) <= 100


def test_policy_contract_requires_explicit_callable_formatters_for_both_sides():
    from src.engine.tooling.lod import ToolLODPolicy

    with pytest.raises(TypeError):
        ToolLODPolicy()
    with pytest.raises(TypeError, match='callable'):
        ToolLODPolicy(input=None, output=lambda value, level: value)
