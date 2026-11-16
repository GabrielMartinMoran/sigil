"""Human tool presentation must survive model serialization and session reloads."""

from __future__ import annotations

import json

import pytest

from src.core.shell.models.run_tool_output import RunToolOutput
from src.core.tooling.outputs import present_output


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

    from src.core.tooling.tool_logger_hub import ToolLoggerHub
    from src.core.tooling.tool_output import ToolOutput
    from src.core.tooling.tool_registrar import ToolRegistrar
    from src.core.tooling.tool_summary import ToolSummary

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
    from src.core.tooling.outputs import register_output

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
