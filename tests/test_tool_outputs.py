"""Human tool presentation must survive model serialization and session reloads."""

from __future__ import annotations

import json

import pytest
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ToolReturnPart

from src.engine.shell.models.run_tool_output import RunToolOutput
from src.engine.tooling.outputs import present_output
from src.server.views import transcript
from src.ui.widgets.chat_log import ToolCallBlock


@pytest.mark.parametrize('serialized', [False, True])
def test_should_show_shell_streams_not_pydantic_or_json_envelopes(serialized: bool) -> None:
    result = [RunToolOutput(returncode=1, stdout='test_a FAILED\n', stderr='NameError: missing')]
    value = [item.model_dump() for item in result] if serialized else result
    shown = present_output('run', value)
    assert shown.summary.status == 'bad'
    assert 'test_a FAILED' in shown.text
    assert 'NameError: missing' in shown.text
    assert 'returncode' not in shown.text
    assert 'stdout' not in shown.text


def test_should_render_persisted_tool_results_identically_in_both_clients() -> None:
    messages = [
        ModelRequest(parts=[ToolReturnPart('run', [{'returncode': 0, 'stdout': '9 passed\n'}], tool_call_id='a')])
    ]
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(messages))
    web = transcript(restored)[0]
    assert web['text'] == '9 passed'
    assert web['summary']['text'] == '9 passed'
    tui = ToolCallBlock('run', {})
    tui.set_result(restored[0].parts[0].content)
    assert tui._summary.text == web['summary']['text']
    assert 'stdout' not in '\n'.join(line.plain for line in tui._body())


def test_should_show_file_content_and_keep_binary_payloads_out_of_the_ui() -> None:
    file = {
        'path': 'a.py',
        'size': 32,
        'truncated': False,
        'binary': False,
        'encoding': 'utf-8',
        'content': 'def f():\n    return 1',
        'total_lines': 2,
    }
    shown = present_output('read_file', file)
    assert shown.text == file['content']
    assert shown.summary.text == '2 lines'
    file.update(binary=True, encoding='base64', content='AAEC')
    assert 'AAEC' not in present_output('read_file', file).text


def test_should_show_loaded_skill_resources_without_metadata_envelope() -> None:
    shown = present_output(
        'load_skill',
        {
            'name': 'clean-code',
            'note': 'Loaded in instructions.',
            'resources': [{'path': 'references/style.md', 'size': 123, 'kind': 'reference'}],
        },
    )
    assert 'clean-code' in shown.text
    assert 'references/style.md' in shown.text
    assert "'resources':" not in shown.text


def test_should_preserve_plain_text_and_decode_only_structured_json_results() -> None:
    assert present_output('unknown', '  source code\n    indent').text == '  source code\n    indent'
    shown = present_output('run', json.dumps([{'returncode': 0, 'stdout': 'ok'}]))
    assert shown.text == 'ok'
    assert (
        present_output('unknown', {'answer': 42, 'nested': {'enabled': True}}).text
        == 'answer: 42\nnested:\n  enabled: yes'
    )


def test_should_allow_tools_to_declare_output_without_changing_model_result() -> None:
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from src.engine.tooling.logging import ToolLoggerHub
    from src.engine.tooling.outputs import ToolOutput
    from src.engine.tooling.presentation import ToolSummary
    from src.engine.tooling.registrar import ToolRegistrar

    events = []
    agent = Agent(TestModel(call_tools=[]))
    logger = ToolLoggerHub([events.append])

    @ToolRegistrar(agent, logger).tool(
        output=lambda data: ToolOutput(f'Found {data["count"]} matches', ToolSummary('3 matches'))
    )
    def custom_output_test() -> dict[str, int]:
        return {'count': 3}

    assert custom_output_test() == {'count': 3}
    assert events[-1].output == 'Found 3 matches'
    assert present_output('custom_output_test', {'count': 3}).text == events[-1].output


def test_should_tolerate_a_broken_presenter_without_reintroducing_repr() -> None:
    from src.engine.tooling.outputs import register_output

    def broken(value: object) -> None:
        raise ValueError('formatter failed')

    register_output('broken_formatter_test', broken)
    assert present_output('broken_formatter_test', {'count': 3}).text == 'count: 3'


def test_should_format_multiple_processes_and_empty_and_timeout_outputs() -> None:
    shown = present_output('run', [{'returncode': 0, 'stdout': 'first'}, {'returncode': 124, 'timed_out': True}])
    assert 'Command 1\nfirst' in shown.text
    assert 'Command 2\nTimed out' in shown.text
    assert shown.summary.status == 'bad'
    assert present_output('run', [{'returncode': 0}]).text == 'Exit 0'


def test_should_bound_output_without_mutating_raw_content() -> None:
    value = {'content': 'x' * 50_000, 'format': 'text_plain'}
    shown = present_output('read_url', value)
    assert shown.text.endswith('(output truncated)')
    assert len(value['content']) == 50_000


def test_should_publish_formatted_live_output_not_the_raw_logging_preview() -> None:
    from types import SimpleNamespace

    from src.engine.tooling.events import ToolCallEvent
    from src.engine.tooling.presentation import ToolSummary
    from src.server.events import EventHub
    from src.server.runtime import ServerRuntime

    runtime = SimpleNamespace(hub=EventHub())
    ServerRuntime._on_tool_event(
        runtime,
        ToolCallEvent(
            'run',
            'success',
            result_preview='[RunToolOutput(returncode=0)]',
            output='9 passed',
            summary=ToolSummary('9 passed', 'good'),
        ),
    )
    event = runtime.hub.replay()[-1]
    assert event.data['preview'] == '9 passed'
    assert 'returncode' not in event.data['preview']


@pytest.mark.parametrize(
    'name, value, expected',
    [
        ('write_file', {'path': 'a.txt', 'bytes_written': 10, 'created': True, 'appended': False}, 'created'),
        (
            'patch_file',
            {
                'path': 'a.txt',
                'mode': 'string',
                'replacements': 1,
                'bytes_before': 10,
                'bytes_after': 12,
                'preview': '-old\n+new',
            },
            '+new',
        ),
        (
            'web_search',
            {'query': 'test', 'results': [{'title': 'Docs', 'url': 'https://example.org', 'snippet': 'Read this'}]},
            'https://example.org',
        ),
        ('update_roadmap', {'items': [{'title': 'Verify', 'status': 'done'}], 'counts': {'done': 1}}, '[done] Verify'),
        ('get_mcp_prompt', {'messages': [{'role': 'user', 'content': 'Instructions'}]}, 'Instructions'),
        ('list_skills', [{'name': 'clean-code', 'description': 'Readable code'}], 'Readable code'),
        ('run_skill_script', {'returncode': 0, 'stdout': 'script output'}, 'script output'),
    ],
)
def test_should_use_domain_presenters_for_builtin_results(name: str, value: object, expected: str) -> None:
    shown = present_output(name, value)
    assert expected in shown.text
    assert shown.summary is not None


def test_should_render_mcp_text_blocks_and_suppress_binary_data() -> None:
    assert present_output('mcp_tool', [{'type': 'text', 'text': 'Useful text'}]).text == 'Useful text'
    assert 'SECRET_BASE64' not in present_output('mcp_tool', {'type': 'image', 'data': 'SECRET_BASE64'}).text
