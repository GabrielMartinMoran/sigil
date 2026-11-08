"""Read-only, content-free cumulative comparisons over a consistent SQLite snapshot.

Run with ``uv run python -m src.engine.context.compaction.simulator --help``.
No provider requests, credential tables, environment files, or filesystem resources
are accessed. Costs are explicit hypothetical scenarios, never measured savings.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelRequest, ModelResponse, ThinkingPart

from src.engine.agents.turn_map import library_run_id
from src.engine.context.budget import DEFAULT_OUTPUT_RESERVE, ContextBudget
from src.engine.context.compaction.constants import (
    LEGACY_REASONING_PERCENT,
    LEGACY_REASONING_STEPS,
    LEGACY_RECENT_TURNS,
    LEGACY_TOOL_RESULT_HEAD_CHARS,
    LEGACY_TOOL_RESULT_MAX_CHARS,
    REASONING_TARGET_PERCENT,
    REASONING_TARGET_STEPS,
)
from src.engine.context.compaction.events import POLICY_VERSION, RECORD_KEY
from src.engine.context.strategy.rewriting import keep_recent_steps, truncate_old_tool_results
from src.engine.context.strategy.turns import split_prompts
from src.engine.context.strategy.windowed import WindowedStrategy
from src.engine.context.tokens import estimate_messages_tokens, estimate_tokens, part_tokens
from src.engine.tooling.lod_policies import ALL_POLICIES


@dataclass(frozen=True, slots=True)
class PriceScenario:
    """Explicit input-only scenario; cache shares are assumptions, not predictions."""

    normal_per_million: float
    read_per_million: float
    write_per_million: float
    read_share: float = 0
    write_share: float = 0

    def __post_init__(self) -> None:
        import math

        values = (self.normal_per_million, self.read_per_million, self.write_per_million)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError('Scenario rates must be finite and non-negative')
        if not 0 <= self.read_share <= 1 or not 0 <= self.write_share <= 1 or self.read_share + self.write_share > 1:
            raise ValueError('Cache shares must be non-negative and sum to at most one')

    def input_cost(self, tokens: int) -> float:
        rate = (
            (1 - self.read_share - self.write_share) * self.normal_per_million
            + self.read_share * self.read_per_million
            + self.write_share * self.write_per_million
        )
        return tokens * rate / 1000000


def _legacy(messages: list[ModelMessage], window: int | None) -> list[ModelMessage]:
    latest = next(
        (library_run_id(message) for message in reversed(messages) if isinstance(message, ModelResponse)), None
    )
    eligible = [message for message in messages if library_run_id(message) == latest]
    kept, _, _ = keep_recent_steps(
        eligible,
        steps=LEGACY_REASONING_STEPS,
        max_tokens=None if window is None else window * LEGACY_REASONING_PERCENT // 100,
    )
    by_id = {id(original): projected for original, projected in zip(eligible, kept)}
    output = []
    for message in messages:
        if id(message) in by_id:
            output.append(by_id[id(message)])
        elif isinstance(message, ModelResponse):
            output.append(
                replace(message, parts=[part for part in message.parts if not isinstance(part, ThinkingPart)])
            )
        else:
            output.append(message)
    turns = split_prompts(output)
    return truncate_old_tool_results(
        turns,
        recent_start=max(0, len(turns) - LEGACY_RECENT_TURNS),
        max_chars=LEGACY_TOOL_RESULT_MAX_CHARS,
        head_chars=LEGACY_TOOL_RESULT_HEAD_CHARS,
        pinned_tools=frozenset({'load_skill', 'read_skill_resource'}),
    )[0]


def _components(messages: Sequence[ModelMessage]) -> dict[str, int]:
    result: dict[str, int] = {}
    for message in messages:
        for part in message.parts:
            kind = part.part_kind
            result[kind] = result.get(kind, 0) + part_tokens(part)
    return result


def simulate(
    messages: Sequence[ModelMessage],
    *,
    window: int | None = None,
    schemas_tokens: int | None = None,
    reasoning_target: int = REASONING_TARGET_STEPS,
    reasoning_percent: int = REASONING_TARGET_PERCENT,
    prices: PriceScenario | None = None,
) -> dict[str, Any]:
    """Sum every request, respecting parallel calls and execution-local records."""
    strategies = {
        'full': None,
        'legacy': None,
        'coordinated': WindowedStrategy(lookup=ALL_POLICIES.get),
        'alternative': WindowedStrategy(
            reasoning_steps=reasoning_target, reasoning_max_percent=reasoning_percent, lookup=ALL_POLICIES.get
        ),
    }
    reports = {}
    for name, strategy in strategies.items():
        history: list[ModelMessage] = []
        cumulative: dict[str, int] = {}
        requests = events = overfull = unknown = fixed_total = peak = 0
        first_change = None
        captured_instructions = 0
        previous_view: list[ModelMessage] = []
        for message in messages:
            clean = replace(
                message, metadata={k: v for k, v in (message.metadata or {}).items() if k != RECORD_KEY} or None
            )
            if not isinstance(clean, ModelResponse):
                history.append(clean)
                continue
            requests += 1
            instruction = next((m.instructions for m in reversed(history) if isinstance(m, ModelRequest)), None)
            captured_instructions += instruction is not None
            fixed = estimate_tokens(instruction or '') + (schemas_tokens or 0)
            fixed_total += fixed
            view = list(history)
            decision = None
            if strategy is not None:
                decision = strategy.view(
                    history, ContextBudget(window=window, instructions=fixed, output_reserve=DEFAULT_OUTPUT_RESERVE)
                )
                view = decision.messages
                overfull += decision.budget_exceeded
                events += len(decision.events) - decision.plan.recorded_events
            elif name == 'legacy':
                view = _legacy(view, window)
            if first_change is None and previous_view and view[: len(previous_view)] != previous_view:
                first_change = requests
            previous_view = view
            for kind, tokens in _components(view).items():
                cumulative[kind] = cumulative.get(kind, 0) + tokens
            unknown += sum(
                isinstance(p, ThinkingPart) and bool(p.signature) and not p.content for m in view for p in m.parts
            )
            peak = max(peak, estimate_messages_tokens(view) + fixed)
            if decision is not None:
                record = {
                    'version': POLICY_VERSION,
                    'state': decision.plan.state.as_row(),
                    'events': [e.as_row() for e in decision.events[decision.plan.recorded_events :]],
                }
                clean = replace(clean, metadata={**(clean.metadata or {}), RECORD_KEY: record})
            history.append(clean)
        reports[name] = {
            'requests': requests,
            'events': events,
            'over_budget_requests': overfull,
            'cumulative_history_tokens': sum(cumulative.values()),
            'cumulative_fixed_tokens': fixed_total,
            'cumulative_input_tokens': sum(cumulative.values()) + fixed_total,
            'components': cumulative,
            'peak_input_tokens': peak,
            'unknown_reasoning_block_requests': unknown,
            'first_prefix_change_request': first_change,
            'requests_with_instruction_snapshot': captured_instructions,
        }
    if prices is not None:
        for report in reports.values():
            report['hypothetical_input_cost_usd'] = prices.input_cost(report['cumulative_input_tokens'])
    return {
        'strategies': reports,
        'window': window,
        'schemas_tokens': schemas_tokens,
        'price_scenario': asdict(prices) if prices is not None else None,
        'alternative': {'reasoning_target': reasoning_target, 'reasoning_percent': reasoning_percent},
        'limitations': [
            'Token estimates are not billing or cache-hit predictions.',
            'Historical tool schemas and native tool costs are unknown unless supplied.',
            'Instruction snapshots are historical, not a reread of skill resources.',
            'Encrypted reasoning size is unknown; quality, TTL and routing are not simulated.',
            'Existing records are ignored to compare policies on the same original transcript.',
        ],
    }


def simulate_database(path: Path, **options: Any) -> dict[str, Any]:
    """BEGIN holds a consistent read snapshot; only transcript/instruction tables are read."""
    connection = sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True, timeout=5)
    try:
        connection.execute('PRAGMA query_only = ON')
        connection.execute('BEGIN')
        rows = connection.execute(
            'SELECT m.execution_id, m.payload, b.data FROM messages m '
            'LEFT JOIN blobs b ON m.instructions_id = b.id ORDER BY m.execution_id, m.seq'
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for execution, payload, instructions in rows:
            value = json.loads(payload)
            if instructions is not None:
                value['instructions'] = bytes(instructions).decode('utf-8')
            grouped.setdefault(execution, []).append(value)
        return {
            'executions': [
                simulate(ModelMessagesTypeAdapter.validate_python(values), **options) for values in grouped.values()
            ]
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True, help='An explicitly selected SQLite database or copy.')
    parser.add_argument('--window', type=int)
    parser.add_argument('--schemas-tokens', type=int)
    parser.add_argument('--reasoning-target', type=int, default=REASONING_TARGET_STEPS)
    parser.add_argument('--reasoning-percent', type=int, default=REASONING_TARGET_PERCENT)
    parser.add_argument('--normal-per-million', type=float)
    parser.add_argument('--read-per-million', type=float)
    parser.add_argument('--write-per-million', type=float)
    parser.add_argument('--assumed-read-share', type=float, default=0)
    parser.add_argument('--assumed-write-share', type=float, default=0)
    args = vars(parser.parse_args())
    rates = [args.pop(key) for key in ('normal_per_million', 'read_per_million', 'write_per_million')]
    read, write = args.pop('assumed_read_share'), args.pop('assumed_write_share')
    if any(rate is not None for rate in rates):
        if any(rate is None for rate in rates):
            parser.error('Supply all three scenario rates, or none')
        args['prices'] = PriceScenario(*rates, read_share=read, write_share=write)
    elif read or write:
        parser.error('Assumed cache shares require explicit scenario rates')
    path = args.pop('database')
    print(json.dumps(simulate_database(path, **args), indent=2))


if __name__ == '__main__':
    main()
