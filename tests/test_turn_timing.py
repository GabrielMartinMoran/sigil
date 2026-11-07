"""Wall-clock attribution without network calls or timing thresholds."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from src.ui.turn_timing import TurnTimings


class ModelRequestNode:
    pass


class CallToolsNode:
    pass


class End:
    pass


def test_should_separate_graph_execution_from_loop_body_and_first_event() -> None:
    now = [0.0]
    timing = TurnTimings(clock=lambda: now[0])

    async def nodes() -> AsyncIterator[object]:
        yield ModelRequestNode()
        now[0] += 2
        timing.stream_event()
        now[0] += 3
        yield CallToolsNode()
        now[0] += 4
        yield End()

    async def scenario() -> None:
        async for _node in timing.trace(nodes()):
            now[0] += 1  # Checkpoints and other loop-body work are not node execution.

    asyncio.run(scenario())
    timing.finish()
    assert timing.elapsed == 12
    assert [(step.phase, step.seconds, step.first_event) for step in timing.steps] == [
        ('ModelRequestNode', 5, 2),
        ('CallToolsNode', 4, None),
    ]
    report = timing.render()
    assert 'outside nodes 3.00s' in report
    assert 'first event 2.00s' in report
    assert 'not pure provider latency' in report


def test_should_keep_failed_node_timing() -> None:
    now = [0.0]
    timing = TurnTimings(clock=lambda: now[0])

    async def nodes() -> AsyncIterator[object]:
        yield ModelRequestNode()
        now[0] = 7
        raise ValueError('provider unavailable')

    async def scenario() -> None:
        try:
            async for _node in timing.trace(nodes()):
                pass
        except ValueError:
            pass

    asyncio.run(scenario())
    timing.finish()
    assert timing.steps[0].seconds == 7
    assert timing.steps[0].interrupted
    assert 'interrupted' in timing.render()


def test_should_show_active_node_without_counting_it_as_outside_work() -> None:
    now = [0.0]
    timing = TurnTimings(clock=lambda: now[0])
    reports: list[str] = []

    async def nodes() -> AsyncIterator[object]:
        yield ModelRequestNode()
        now[0] = 6
        reports.append(timing.render())
        yield End()

    async def scenario() -> None:
        async for _node in timing.trace(nodes()):
            pass

    asyncio.run(scenario())
    assert 'outside nodes 0.00s' in reports[0]
    assert 'ModelRequestNode: 6.00s · running' in reports[0]


def test_should_bound_retained_nodes_without_losing_total_time() -> None:
    from src.ui.turn_timing import MAX_TIMING_STEPS

    now = [0.0]
    timing = TurnTimings(clock=lambda: now[0])

    async def nodes() -> AsyncIterator[object]:
        for _ in range(MAX_TIMING_STEPS + 10):
            now[0] += 1
            yield ModelRequestNode()
        now[0] += 1
        yield End()

    async def scenario() -> None:
        async for _node in timing.trace(nodes()):
            pass

    asyncio.run(scenario())
    timing.finish()
    assert len(timing.steps) == MAX_TIMING_STEPS
    assert timing.elapsed == MAX_TIMING_STEPS + 11
    assert 'outside nodes 1.00s' in timing.render()
