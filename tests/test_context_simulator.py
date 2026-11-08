"""The simulator is cumulative, deterministic, and never writes or exports payloads."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.engine.context.compaction.simulator import PriceScenario, simulate, simulate_database
from src.engine.sessions.session_store import SessionStore


def transcript(steps: int = 35) -> list:
    messages = [ModelRequest(parts=[UserPromptPart(content='PRIVATE task')], instructions='fixed instructions')]
    for index in range(steps):
        messages.extend(
            [
                ModelResponse(
                    parts=[
                        ThinkingPart(content='thought ' * 100),
                        ToolCallPart(tool_name='foreign', args={'q': 'x' * 5000}, tool_call_id=str(index)),
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(tool_name='foreign', content='PRIVATE payload ' * 600, tool_call_id=str(index))
                    ],
                    instructions='fixed instructions',
                ),
            ]
        )
    return messages


def test_simulator_sums_every_request_not_only_maintenance_events():
    messages = transcript()
    result = simulate(messages)
    assert result == simulate(messages)
    assert 'PRIVATE' not in json.dumps(result)
    full = result['strategies']['full']
    coordinated = result['strategies']['coordinated']
    assert full['requests'] == coordinated['requests'] == 35
    assert coordinated['events'] > 0
    assert coordinated['cumulative_input_tokens'] < full['cumulative_input_tokens']
    assert full['cumulative_history_tokens'] > full['peak_input_tokens']
    assert result['schemas_tokens'] is None
    assert result['limitations']


def test_database_snapshot_is_read_only_and_does_not_read_credentials(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'home')
    session = store.create_session()
    session.messages = transcript(4)
    store.save(session)
    database = store.database.path
    with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True)) as connection:
        before = connection.execute('SELECT sum(length(payload)) FROM messages').fetchone()
    result = simulate_database(database)
    with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True)) as connection:
        after = connection.execute('SELECT sum(length(payload)) FROM messages').fetchone()
    assert before == after
    assert len(result['executions']) == 1
    assert 'PRIVATE' not in json.dumps(result)
    assert result['executions'][0]['strategies']['full']['requests'] == 4


def test_hypothetical_prices_separate_reads_writes_and_normal_input():
    prices = PriceScenario(
        normal_per_million=10, read_per_million=1, write_per_million=20, read_share=0.5, write_share=0.25
    )
    assert prices.input_cost(1000000) == 8
    result = simulate(transcript(2), prices=prices)
    full = result['strategies']['full']
    assert full['hypothetical_input_cost_usd'] == prices.input_cost(full['cumulative_input_tokens'])
    assert result['price_scenario']['read_share'] == 0.5
    assert result['price_scenario']['write_per_million'] == 20


def test_invalid_hypothetical_shares_are_rejected():
    with pytest.raises(ValueError, match='sum'):
        PriceScenario(1, 1, 1, read_share=0.8, write_share=0.8)


def test_unknown_reasoning_is_not_converted_into_synthetic_billed_tokens():
    result = simulate(
        [
            ModelRequest(parts=[UserPromptPart(content='go')]),
            ModelResponse(parts=[ThinkingPart(content='', signature='opaque'), TextPart(content='ok')]),
            ModelRequest(parts=[UserPromptPart(content='next')]),
            ModelResponse(parts=[TextPart(content='done')]),
        ]
    )
    full = result['strategies']['full']
    assert full['unknown_reasoning_block_requests'] == 1
    assert full['components']['thinking'] == 0
