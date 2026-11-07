"""Bounded, content-free wall-clock diagnostics for the most recent TUI turn."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

MAX_TIMING_STEPS = 200


@dataclass(frozen=True, slots=True)
class StepTiming:
    phase: str
    seconds: float
    first_event: float | None
    interrupted: bool


class TurnTimings:
    """Measure graph advancement separately from work in the caller's loop body.

    Model nodes include prompt preparation, transport, retries, streaming and UI
    consumption. They are not a measurement of the provider alone. No prompts,
    arguments, outputs or credentials are retained.
    """

    def __init__(self, *, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._started = clock()
        self._finished: float | None = None
        self._phase = ''
        self._node_started = self._started
        self._first_event: float | None = None
        self._node_seconds = 0.0
        self.steps: deque[StepTiming] = deque(maxlen=MAX_TIMING_STEPS)

    @property
    def elapsed(self) -> float:
        end = self._clock() if self._finished is None else self._finished
        return max(0.0, end - self._started)

    def finish(self) -> None:
        self._finished = self._clock()

    def stream_event(self) -> None:
        if self._phase == 'ModelRequestNode' and self._first_event is None:
            self._first_event = self._clock() - self._node_started

    async def trace(self, source: AsyncIterable[Any]) -> AsyncIterator[Any]:
        iterator = aiter(source)
        previous = ''
        while True:
            self._phase = previous
            self._node_started = self._clock()
            self._first_event = None
            interrupted = True
            try:
                node = await anext(iterator)
                interrupted = False
            except StopAsyncIteration:
                return
            finally:
                if previous and previous != 'End':
                    self._record(interrupted)
                self._phase = ''
            yield node
            previous = type(node).__name__

    def _record(self, interrupted: bool) -> None:
        seconds = self._clock() - self._node_started
        self._node_seconds += seconds
        self.steps.append(StepTiming(self._phase, seconds, self._first_event, interrupted))

    def render(self) -> str:
        active = max(0.0, self._clock() - self._node_started) if self._phase and self._phase != 'End' else 0.0
        outside = max(0.0, self.elapsed - self._node_seconds - active)
        lines = [
            f'Turn timing: total {self.elapsed:.2f}s · outside nodes {outside:.2f}s',
            'ModelRequestNode includes preparation, retries and stream/UI consumption; not pure provider latency.',
            f'Last {len(self.steps)} nodes (up to {MAX_TIMING_STEPS} retained):',
        ]
        if self._phase and self._phase != 'End':
            lines.append(f'  {self._phase}: {active:.2f}s · running')
        for step in self.steps:
            first = '' if step.first_event is None else f' · first event {step.first_event:.2f}s'
            state = ' · interrupted' if step.interrupted else ''
            lines.append(f'  {step.phase}: {step.seconds:.2f}s{first}{state}')
        return '\n'.join(lines)
