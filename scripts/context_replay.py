"""Compare deterministic compaction thresholds without network or credential access."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from pydantic_ai.messages import ModelMessagesTypeAdapter

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.policy import CompactionPolicy
from src.engine.context.replay import replay


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('transcript', type=Path, help='Pydantic AI message-history JSON export')
    parser.add_argument('--window', type=int, required=True)
    parser.add_argument('--fixed-tokens', type=int, default=0)
    parser.add_argument('--output-reserve', type=int, default=8000)
    parser.add_argument('--safety-margin-percent', type=int, default=5)
    parser.add_argument('--uncached-price', type=Decimal, required=True, help='USD per million input tokens')
    parser.add_argument('--cached-price', type=Decimal, required=True, help='USD per million matching-prefix tokens')
    args = parser.parse_args()
    if args.window <= 0 or min(args.fixed_tokens, args.output_reserve, args.uncached_price, args.cached_price) < 0:
        parser.error('window must be positive; token reservations and prices must be non-negative')
    messages = ModelMessagesTypeAdapter.validate_json(args.transcript.read_bytes())
    budget = ContextBudget(
        window=args.window,
        instructions=args.fixed_tokens,
        output_reserve=args.output_reserve,
        safety_margin_percent=args.safety_margin_percent,
    )
    results = []
    for trigger in (60, 75, 85, 95):
        policy = CompactionPolicy(trigger_percent=trigger, target_percent=min(60, trigger - 15))
        report = replay(messages, budget, policy)
        cost = report.input_cost(uncached=args.uncached_price, cached=args.cached_price)
        results.append(
            {
                'trigger_percent': trigger,
                'target_percent': policy.target_percent,
                **asdict(report),
                'hypothetical_input_cost_usd': str(cost),
            }
        )
    print(
        json.dumps(
            {
                'kind': 'local deterministic replay; not provider cache usage',
                'limitations': [
                    'No LLM summaries or quality evaluation',
                    'Stops at first unsafe projection',
                    'Matching parts are not measured cache hits',
                    'Input-only prices exclude cache writes and auxiliary requests',
                ],
                'results': results,
            },
            indent=2,
        )
    )


if __name__ == '__main__':
    main()
