"""Agent turn execution: event stream and checkpoints."""

from __future__ import annotations

import time
from typing import Any

from pydantic_ai.capabilities import ProcessEventStream
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from pydantic_ai.usage import UsageLimits
from textual import work

from src.config_provider import ConfigProvider
from src.engine.agents.step_limiter import StepLimiter, StepLimitExceeded
from src.ui.efforts import EFFORTS
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput

CHECKPOINT_INTERVAL = 1.0
"""Minimum seconds between checkpoints during a turn (the final save is mandatory)."""


class TurnMixin:
    """Runs the agent turn against the app.

    It expects the app to define ``_agent``, ``_history``, ``_session``, ``_store``,
    ``_current_run``, ``_last_checkpoint_at`` and ``_update_status``.
    """

    _agent: Any
    _history: list[Any]
    _session: Any
    _store: Any
    _current_run: Any
    _last_checkpoint_at: float

    def model_settings(self) -> dict[str, Any] | None:
        """Per-request settings: the configured reasoning effort."""
        effort = ConfigProvider.REASONING_EFFORT
        if effort in EFFORTS:
            return {'thinking': effort}
        return None

    def checkpoint(self, *, force: bool = False) -> None:
        """Persists the progress of the current turn, at most once per second.

        It is called after every agent iteration and every stream
        event. If the process dies halfway through a long turn, the session on
        disk keeps what was completed up to the last checkpoint. It only writes
        when the history changed and the throttle window passed; the final
        save of the turn is mandatory and does not go through here.
        """
        run = self._current_run
        if run is None:
            return
        messages = run.all_messages()
        if not messages:
            return
        current = self._session.messages
        if len(messages) == len(current) and current and messages[-1] is current[-1]:
            return
        now = time.monotonic()
        if not force and now - self._last_checkpoint_at < CHECKPOINT_INTERVAL:
            return
        self._last_checkpoint_at = now
        self._session.messages = list(messages)
        self._store.save(self._session)
        # The bar updates per step, not only when the turn closes.
        self.update_status(messages)
        # The debug view too (it is a no-op if closed).
        refresh_debug = getattr(self, 'refresh_debug', None)
        if callable(refresh_debug):
            refresh_debug(messages)

    def turn_handler(self, limiter: StepLimiter, chat: ChatLog) -> Any:
        """Stream handler that feeds the chat and the checkpoints."""

        async def handler(_ctx: Any, events: Any) -> None:
            async for event in events:
                limiter.count(event)
                # Checkpoint per event: as soon as the run adds a message to
                # the history (e.g. the user prompt) it lands on disk.
                self.checkpoint()
                await self.dispatch_event(event, chat)

        return handler

    async def dispatch_event(self, event: Any, chat: ChatLog) -> None:
        """Translates a stream event into what the chat shows."""
        if isinstance(event, PartStartEvent):
            if event.index == 0:
                await chat.begin_response()
            part = event.part
            if isinstance(part, ThinkingPart) and part.content:
                await chat.add_thinking(part.content)
            elif isinstance(part, TextPart) and part.content:
                await chat.add_text(part.content)
        elif isinstance(event, PartDeltaEvent):
            delta = event.delta
            if isinstance(delta, TextPartDelta) and delta.content_delta:
                await chat.add_text(delta.content_delta)
            elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                await chat.add_thinking(delta.content_delta)
        elif isinstance(event, FunctionToolResultEvent):
            ok = getattr(event.part, 'outcome', 'success') == 'success'
            await chat.add_tool_result(event.part.tool_name, event.part.content, ok=ok)

    @work(exclusive=True)
    async def run_turn(self, text: str) -> None:
        """Runs a full turn, persisting progress along the way."""
        chat = self.query_one(ChatLog)
        prompt = self.query_one(PromptInput)
        limiter = StepLimiter(ConfigProvider.MAX_STEPS)
        handler = self.turn_handler(limiter, chat)
        prompt.disabled = True
        # `agent.iter` (instead of `agent.run`) keeps the run accessible: if the
        # turn is aborted by the step limit or by an error, `run.all_messages()`
        # returns what was completed and the session does not lose the context.
        #
        # `request_limit=None` disables the 50-request cap pydantic-ai
        # applies by default: Sigil's step limit (`SIGIL_MAX_STEPS`) already
        # bounds the turn, and the default would cut earlier with a `UsageLimitExceeded`
        # the user did not configure.
        run: Any = None
        history = self._history
        # The first checkpoint of the turn (the user prompt) is saved right away.
        self._last_checkpoint_at = 0.0
        try:
            async with self._agent.iter(
                text,
                message_history=self._history,
                capabilities=[ProcessEventStream(handler=handler)],
                model_settings=self.model_settings(),
                usage_limits=UsageLimits(request_limit=None),
            ) as run:
                self._current_run = run
                async for _node in run:
                    # Checkpoint per iteration: every completed node (model
                    # response, tool batch) gets persisted.
                    self.checkpoint()
            history = run.all_messages()
        except StepLimitExceeded:
            if run is not None:
                history = run.all_messages()
            await chat.add_notice(
                f'Step limit of {ConfigProvider.MAX_STEPS} reached; turn aborted.',
                style='bold red',
            )
        except Exception as exc:  # noqa: BLE001
            if run is not None:
                history = run.all_messages()
            await chat.add_notice(f'Error: {exc}', style='bold red')
        finally:
            self._current_run = None
            prompt.disabled = False
            prompt.focus()
            chat.finalize()

        self._history = history
        self._session.messages = self._history
        self._store.save(self._session)
        self.update_status()
        # The debug view shows the cost of the turn just closed.
        refresh_debug = getattr(self, 'refresh_debug', None)
        if callable(refresh_debug):
            refresh_debug()
