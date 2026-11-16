"""Real tool models have loss-aware, idempotent input and output projections."""

from __future__ import annotations

import json

import pytest
from pydantic_ai.messages import BinaryContent, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart

from src.core.context.compaction.compaction_policy import CompactionPolicy
from src.core.context.compaction.coordinator import apply, exceptions, plan
from src.core.files.models.file_content import FileContent
from src.core.files.models.patch_result import PatchResult
from src.core.files.models.write_result import WriteResult
from src.core.interaction.models.question_answer import QuestionAnswer
from src.core.interaction.models.question_result import QuestionResult
from src.core.mcp.tool_models.prompt_info import PromptInfo
from src.core.mcp.tool_models.prompt_message_info import PromptMessageInfo
from src.core.mcp.tool_models.prompt_result_info import PromptResultInfo
from src.core.mcp.tool_models.resource_content import ResourceContent
from src.core.mcp.tool_models.resource_info import ResourceInfo
from src.core.mcp.tool_models.server_status import ServerStatus
from src.core.roadmap.models.roadmap_snapshot import RoadmapSnapshot
from src.core.shell.models.run_tool_output import RunToolOutput
from src.core.skills.tool_models.loaded_skill import LoadedSkill
from src.core.skills.tool_models.resource_content import ResourceContent as SkillResourceContent
from src.core.skills.tool_models.script_run_result import ScriptRunResult
from src.core.skills.tool_models.skill_summary import SkillSummary
from src.core.tooling.compaction import GENERIC_COMPACTOR, compact_json, shorten
from src.core.tooling.compaction_policies import ALL_COMPACTORS
from src.core.tooling.encoded import encoded
from src.core.tooling.normalize import normalize
from src.core.tooling.tool_compactor import ToolCompactor
from src.core.web.models.page_content import PageContent
from src.core.web.models.search_response import SearchResponse
from src.core.web.models.search_result import SearchResult

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


@pytest.mark.parametrize('name', sorted(ALL_COMPACTORS))
def test_every_registered_compactor_handles_real_output_and_stored_json_identically(name) -> None:
    value = samples()[name]
    compactor = ALL_COMPACTORS[name]
    stored = normalize(value)
    live = compactor.compact_output(value)
    replay = compactor.compact_output(stored)
    assert encoded(live) == encoded(replay)
    # Compacting a compacted value is a no-op, and the original is never mutated.
    assert encoded(compactor.compact_output(live)) == encoded(live)
    assert len(encoded(live)) <= len(encoded(stored))
    assert encoded(value) == encoded(stored)


@pytest.mark.parametrize('name', sorted(ALL_COMPACTORS))
def test_every_compactor_handles_its_arguments(name) -> None:
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
    compactor = ALL_COMPACTORS[name]
    reduced = compactor.compact_input(args)
    assert isinstance(reduced, dict)
    assert len(encoded(reduced)) <= len(encoded(args))


def test_file_compaction_handles_plain_text_and_preserves_line_breaks() -> None:
    content = 'first line\nsecond line\n' * 100
    compactor = ALL_COMPACTORS['read_file']

    plain = compactor.compact_output(content)
    assert plain.startswith('first line\nsecond line\n')
    assert 'tool result truncated:' in plain
    assert len(plain) <= 800

    structured = compactor.compact_output({'path': 'file.py', 'content': content})
    assert structured['path'] == 'file.py'
    assert structured['content'].startswith('first line\nsecond line\n')
    assert len(structured['content']) <= 800


def test_generic_json_preserves_validity_and_aggregate_budget_with_escaping() -> None:
    value = {'rows': ['"\\\n' * 1000 for _ in range(20)]}
    for original in (value, '"\\\n' * 2000):
        result = compact_json(original, 500)
        assert json.loads(result) is not None
        assert len(result) <= 500


def test_failed_structured_tool_returns_are_not_interpreted_as_success_models() -> None:
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
    planned = plan(messages, pressure=True, lookup=ALL_COMPACTORS.get)
    reduced, _ = apply(messages, planned, lookup=ALL_COMPACTORS.get)
    result = reduced[1].parts[0]
    assert result.outcome == 'failed'
    assert 'SearchError' in result.content
    assert result.tool_call_id == 'c'


def test_opaque_external_binary_is_preserved_and_reported() -> None:
    binary = BinaryContent(data=b'\x00' * 5000, media_type='application/octet-stream')
    messages = [
        ModelResponse(parts=[ToolCallPart(tool_name='foreign', args={'q': LONG}, tool_call_id='c')]),
        ModelRequest(parts=[ToolReturnPart(tool_name='foreign', content=[binary], tool_call_id='c')]),
        ModelResponse(parts=[TextPart(content='ok')]),
    ]
    policy = CompactionPolicy(recent_tool_calls=0, recent_reasoning_blocks=0)
    planned = plan(messages, policy=policy, pressure=True)
    reduced, _ = apply(messages, planned)
    assert reduced[1].parts[0].content == [binary]
    assert 'opaque_binary_or_multimodal' in exceptions(messages, planned, None)


def test_stream_truncation_flags_and_exact_omitted_count_are_honest() -> None:
    result = ALL_COMPACTORS['run'].compact_output([RunToolOutput(returncode=1, stdout=LONG)])[0]
    assert result['stdout_truncated'] is True
    shortened = shorten('a' * 1000, 100)
    prefix, notice = shortened.split('\n\n', 1)
    assert f'{1000 - len(prefix):,}' in notice
    assert len(shortened) <= 100


def test_user_decisions_and_identity_are_not_cut_to_hit_own_semantic_sizes() -> None:
    value = QuestionResult(answers=[QuestionAnswer(question='Confirm?', answer='Do not ' + LONG)])
    assert ALL_COMPACTORS['ask_user'].compact_output(value)['answers'][0]['answer'] == 'Do not ' + LONG
    source = 'https://test/' + 'long/path/' * 100
    assert ALL_COMPACTORS['view_image'].compact_input({'source': source})['source'] == source


def test_generic_nested_failure_metadata_survives_even_when_it_exceeds_the_target() -> None:
    value = {'rows': ['x' * 10000], 'envelope': {'error': 'nested failure ' * 100, 'status': 'failed'}}
    reduced = GENERIC_COMPACTOR.compact_output(value)
    assert reduced['metadata']['envelope']['error'] == value['envelope']['error']
    assert reduced['metadata']['envelope']['status'] == 'failed'


def test_generic_policy_accepts_json_schema_type_arrays_without_treating_them_as_binary() -> None:
    value = {'type': ['string', 'null'], 'examples': ['x' * 5000]}
    reduced = GENERIC_COMPACTOR.compact_output(value)
    assert reduced['truncated'] is True
    assert len(json.dumps(reduced)) <= 500


def test_compactor_contract_requires_explicit_callable_formatters_for_both_sides() -> None:
    with pytest.raises(TypeError, match='callable'):
        ToolCompactor(input=None, output=lambda value: value)


def test_a_compactor_never_expands_a_value_that_it_cannot_shrink() -> None:
    assert GENERIC_COMPACTOR.compact_output('short') == 'short'
    assert ALL_COMPACTORS['read_file'].compact_output({'path': 'a.py'}) == {'path': 'a.py'}
