"""Unified write results render correctly live and after JSON serialization."""

import json

from src.core.tooling.outputs import present_output


def test_unified_write_result_has_correct_presentation() -> None:
    result = {'path': 'hello.py', 'created': True, 'operations': 2, 'bytes_after': 42}
    for value in (result, json.dumps(result)):
        shown = present_output('write_file', value)
        assert 'hello.py' in shown.text
        assert shown.summary is not None
        assert shown.summary.status != 'bad'
