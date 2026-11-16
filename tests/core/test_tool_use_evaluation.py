"""Contract for the deterministic tool-use evaluation harness."""

import asyncio

from scripts.evaluate_tool_use import run_offline


def test_offline_sample_reports_task_success_and_rounds() -> None:
    report = asyncio.run(run_offline())
    assert report['mode'] == 'scripted-offline'
    assert report['model_quality_measured'] is False
    sample = report['samples'][0]
    assert sample['success'] is True
    assert sample['requests'] == 3
    assert sample['tool_rounds'] == 2
    assert sample['tool_calls'] == 2
    assert sample['argument_errors'] == 0
    assert sample['estimated_history_tokens'] > 0
