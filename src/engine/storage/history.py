"""Reading and writing a transcript as rows.

A checkpoint stops being "serialize the session" and becomes "insert what is new".
The payload of a message stays JSON on purpose: **the message shape belongs to
pydantic-ai**, and it changes — `CompactionPart`, `RetryPromptPart`,
`provider_details`, signed reasoning. A table per part kind would need a schema step
every time the library adds one, and a lossy one. What is *not* opaque is what
Sigil queries: the kind, the usage numbers and the instructions are copied into
columns at write time.
"""

from __future__ import annotations

import json
import sqlite3

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from src.engine.agents.turn_map import turn_id_of, turns_of
from src.engine.storage.blobs import INSTRUCTIONS, Blobs
from src.engine.storage.database import Database

TAIL_ROWS = 8
"""Rows rewritten on every save, on top of the new ones.

`recover_history` and `repair_messages` rewrite the *tail*: an unanswered call gets
a synthesized result, a suspended response is stamped `interrupted`. A blind append
would keep the broken version and add a second answer. Rewriting eight rows costs
nothing and covers every repair the recovery path performs.
"""


def _usage(payload: dict) -> dict[str, object]:
    """The numbers lifted out of a message's `usage`, when it has one."""
    usage = payload.get('usage')
    if not isinstance(usage, dict):
        return {}
    cost = usage.get('cost')
    return {
        'input_tokens': usage.get('input_tokens'),
        'cache_read': usage.get('cache_read_tokens'),
        'cache_write': usage.get('cache_write_tokens'),
        'output_tokens': usage.get('output_tokens'),
        'cost': float(cost) if isinstance(cost, (int, float)) else None,
    }


def write_history(
    database: Database,
    blobs: Blobs,
    execution_id: str,
    messages: list[ModelMessage],
    *,
    conn: sqlite3.Connection,
) -> None:
    """Persists ``messages`` as the history of ``execution_id``, appending the new.

    The caller owns the transaction: a checkpoint writes the session row, the
    executions and their transcripts in one unit, so a crash leaves the previous
    checkpoint intact instead of a half-written one.
    """
    expected = len(messages)
    stored = database.one(
        'SELECT max(seq) AS high, count(*) AS total FROM messages WHERE execution_id = ?',
        (execution_id,),
    )
    high = -1 if stored is None or stored['high'] is None else int(stored['high'])
    total = 0 if stored is None else int(stored['total'] or 0)

    if total > expected:
        # A history that came back shorter was compacted or discarded.
        conn.execute('DELETE FROM messages WHERE execution_id = ? AND seq >= ?', (execution_id, expected))
        conn.execute(
            'DELETE FROM turns WHERE execution_id = ? '
            'AND id NOT IN (SELECT turn_id FROM messages WHERE execution_id = ?)',
            (execution_id, execution_id),
        )
        high = min(high, expected - 1)

    start = max(0, high + 1 - TAIL_ROWS) if high >= 0 else 0
    if start >= expected:
        return

    tail = ModelMessagesTypeAdapter.dump_python(messages[start:], mode='json')
    touched: dict[str, int] = {}

    for offset, payload in enumerate(tail):
        seq = start + offset
        instructions = payload.pop('instructions', None)
        usage = _usage(payload)
        turn_id = turn_id_of(messages, seq)
        _ensure_turn(conn, execution_id, turn_id, messages, seq, touched)
        row = (
            execution_id,
            turn_id,
            seq,
            str(payload.get('kind') or ''),
            json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
            blobs.put_text(instructions, kind=INSTRUCTIONS, conn=conn)
            if isinstance(instructions, str) and instructions
            else None,
            usage.get('input_tokens'),
            usage.get('cache_read'),
            usage.get('cache_write'),
            usage.get('output_tokens'),
            usage.get('cost'),
            payload.get('timestamp'),
        )
        conn.execute(
            'INSERT INTO messages (execution_id, turn_id, seq, kind, payload, instructions_id, '
            'input_tokens, cache_read, cache_write, output_tokens, cost, timestamp) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
            'ON CONFLICT(execution_id, seq) DO UPDATE SET '
            'turn_id = excluded.turn_id, kind = excluded.kind, payload = excluded.payload, '
            'instructions_id = excluded.instructions_id, input_tokens = excluded.input_tokens, '
            'cache_read = excluded.cache_read, cache_write = excluded.cache_write, '
            'output_tokens = excluded.output_tokens, cost = excluded.cost, '
            'timestamp = excluded.timestamp',
            row,
        )
        touched[turn_id] = touched.get(turn_id, 0) + 1

    for turn_id in touched:
        _refresh_turn(conn, turn_id)


def _ensure_turn(
    conn: sqlite3.Connection,
    execution_id: str,
    turn_id: str,
    messages: list[ModelMessage],
    seq: int,
    touched: dict[str, int],
) -> None:
    """Inserts the turn of ``seq`` if it is new, in the same transaction.

    `messages.turn_id` is `NOT NULL`, so the turn row cannot arrive later: a
    nullable column plus a backfill is how inconsistent data starts.
    """
    if turn_id in touched:
        return
    span = next((item for item in turns_of(messages) if item.turn_id == turn_id), None)
    started = _timestamp_of(messages, span.index if span else seq)
    conn.execute(
        'INSERT OR IGNORE INTO turns (id, execution_id, origin, started_at, finished_at, outcome, library_run_id) '
        "VALUES (?, ?, 'user', ?, ?, 'done', ?)",
        (turn_id, execution_id, started, started, span.library_run_id if span else None),
    )


def _timestamp_of(messages: list[ModelMessage], index: int) -> str:
    from datetime import UTC, datetime

    value = getattr(messages[index], 'timestamp', None) if index < len(messages) else None
    return value.isoformat() if hasattr(value, 'isoformat') else datetime.now(UTC).isoformat()


def _refresh_turn(conn: sqlite3.Connection, turn_id: str) -> None:
    """Recomputes a turn's totals from the rows that belong to it."""
    conn.execute(
        'UPDATE turns SET '
        'finished_at = COALESCE('
        '  (SELECT max(m.timestamp) FROM messages m WHERE m.turn_id = turns.id), finished_at), '
        "outcome = COALESCE(outcome, 'done'), "
        "steps = (SELECT count(*) FROM messages m WHERE m.turn_id = turns.id AND m.kind = 'response'), "
        'cost = (SELECT sum(m.cost) FROM messages m WHERE m.turn_id = turns.id) '
        'WHERE id = ?',
        (turn_id,),
    )


def read_history(database: Database, blobs: Blobs, execution_id: str) -> list[ModelMessage]:
    """The whole transcript of an execution, in order.

    The instructions come back from the blob cache, one entry per distinct block,
    and are **assigned, never re-encoded**: a `str` is shared, not copied. Splicing
    them into the JSON text and re-validating with `validate_json` is the obvious
    implementation and takes six times as long.
    """
    rows = database.query(
        'SELECT payload, instructions_id FROM messages WHERE execution_id = ? ORDER BY seq',
        (execution_id,),
    )
    rebuilt: list[dict] = []
    for row in rows:
        message = json.loads(row['payload'])
        instructions_id = row['instructions_id']
        if instructions_id is not None:
            message['instructions'] = blobs.text(int(instructions_id))
        rebuilt.append(message)
    if not rebuilt:
        return []
    return list(ModelMessagesTypeAdapter.validate_python(rebuilt))


def history_size(database: Database, execution_id: str) -> int:
    """How many messages an execution has. Used to test the delta write."""
    value = database.value('SELECT count(*) FROM messages WHERE execution_id = ?', (execution_id,), 0)
    return int(value or 0)


def turn_usage(database: Database, execution_id: str) -> sqlite3.Row | None:
    """Cost and cache totals of an execution, as an aggregate over indexed columns."""
    return database.one(
        'SELECT count(*) AS messages, sum(input_tokens) AS input, sum(cache_read) AS cache_read, '
        'sum(cache_write) AS cache_write, sum(output_tokens) AS output, sum(cost) AS cost '
        'FROM messages WHERE execution_id = ?',
        (execution_id,),
    )


__all__ = ['TAIL_ROWS', 'history_size', 'read_history', 'turn_usage', 'write_history']
