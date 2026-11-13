"""Agent debug view: `alt+d` replaces the chat with what the agent sees.

**Two sources, one screen.** In process the report is measured here: the history,
the meter and the captured request of the run this process is executing. An
attached terminal runs nothing, so the same screen is built from the service's
projection (`/api/context` and `/api/state`) and from what the service does *not*
project it says so: a local `0` about a session somebody else is running is a
measurement this process never made, and `model: ?` next to a model the service
has selected is the same mistake with a question mark.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai.messages import ModelMessage
from rich.text import Text
from textual import work
from textual.css.query import NoMatches
from textual.widgets import Static

from src.engine.context.meter.debug import DebugReport, build_debug_report
from src.engine.context.meter.request import RequestSnapshot
from src.ui.service_client import ServiceClientError
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.debug_view import AgentDebugView
from src.ui.widgets.prompt_input import PromptInput

_EMPTY_REQUEST = RequestSnapshot()
"""Snapshot when no request has run in the session yet."""

_REFRESH_DELAY = 0.15
"""Coalescing seconds. During a turn the stream events arrive in
bursts and re-projecting the history on each one is wasted work: the body is
repainted once per window and no state is lost, because everything is derived
from the history."""

SERVICE_DEBUG = 'debug-service'
"""The worker that reads `/api/context` for an attached view.

Exclusive: a burst of turn events asks for one repaint, and the newest read is the
one that wins. A separate group, because it must not cancel the event stream or a
turn.
"""

NOT_MEASURED = 'not measured in this process — the service owns the turn'
"""What a section says when only the service could have measured it.

Attached, the cache decisions and the compaction of each request happen in the
process that runs the turn, and it does not publish them. Saying so is the honest
answer; printing the local meter's `0` is not.
"""

NOT_REPORTED = 'not reported by the service'
"""What a value says when the projection does not carry it."""

NOT_MEASURED_SECTIONS = (
    ('prompt cache', 'reads, writes and the read share of each request'),
    ('compaction', 'the policy, the thresholds and the events of each turn'),
    ('request shape', 'the instruction blocks and tool schemas of each request'),
    ('turn timing', 'the steps of the turn in flight'),
)
"""The measurements that live only where the turn runs, and what each one is."""

_BUDGET_FIELDS = (
    ('tools', 'tools'),
    ('instructions', 'instructions'),
    ('output_reserve', 'output reserve'),
    ('safety_reserve', 'safety reserve'),
)


@dataclass(frozen=True, slots=True)
class ServiceDebugReport:
    """What an attached terminal can honestly say about a session it does not run.

    Every field is a value the service sent, or ``None`` when it sent none. There is
    no local meter, no local history and no local run behind this report: a zero
    here would be a measurement of a conversation this process has never seen.
    """

    session: str = ''
    agent: str = ''
    model: str = ''
    effort: str = ''
    window: int | None = None
    used: int | None = None
    percent: float | None = None
    measured: int | None = None
    projected: int | None = None
    budget: Mapping[str, Any] | None = None
    cost_total: str | None = None
    cost_requests: int | None = None
    cost_estimated: bool = False
    context_read: bool = False
    """Whether `/api/context` answered at all.

    ``False`` is not a session at zero: it is a route that did not answer, and the
    report says exactly that instead of filling the gap with numbers.
    """

    @classmethod
    def from_projection(cls, context: Mapping[str, Any] | None, state: Mapping[str, Any]) -> ServiceDebugReport:
        """Maps the two projections onto what the report prints.

        The projection is JSON the service owns, so this is the one place its keys
        become the report's fields: nothing else in the view reads a payload.
        """
        model = state.get('model')
        model = model if isinstance(model, Mapping) else {}
        session = state.get('session')
        session = session if isinstance(session, Mapping) else {}
        read = context or {}
        budget = read.get('budget')
        budget = budget if isinstance(budget, Mapping) else {}
        cost = read.get('cost')
        cost = cost if isinstance(cost, Mapping) else {}
        total = cost.get('total')
        return cls(
            session=_session_label(session),
            agent=str(session.get('agent') or ''),
            model=str(model.get('label') or model.get('model') or ''),
            effort=str(model.get('thinking') or ''),
            window=_whole(read.get('window')),
            used=_whole(read.get('used')),
            percent=_ratio(read.get('percent')),
            measured=_whole(read.get('measured')),
            projected=_whole(read.get('projected')),
            budget=dict(budget) if budget else None,
            cost_total=None if total is None else str(total),
            cost_requests=_whole(cost.get('requests')),
            cost_estimated=bool(cost.get('approximate')),
            context_read=context is not None,
        )


def _session_label(session: Mapping[str, Any]) -> str:
    """The session as one line: its short id and, when the service titled it, the title."""
    short = str(session.get('short_id') or '')
    title = str(session.get('title') or '')
    if short and title:
        return f'{short} · {title}'
    return short or title


def _tokens(value: int | None) -> str:
    return NOT_REPORTED if value is None else f'{value:,} tokens'


def _used(report: ServiceDebugReport) -> str:
    """The projected usage, with the share only when the service sent both halves."""
    if report.used is None:
        return NOT_REPORTED
    if report.percent is None:
        return f'{report.used:,} tokens'
    return f'{report.used:,} ({report.percent * 100:.0f}%)'


def _budget(budget: Mapping[str, Any]) -> str:
    """The fixed cost of a request, as the service measured it."""
    parts = []
    for key, label in _BUDGET_FIELDS:
        value = _whole(budget.get(key))
        parts.append(f'{label} {NOT_REPORTED if value is None else f"{value:,}"}')
    return ' · '.join(parts)


def _cost(report: ServiceDebugReport) -> str:
    if report.cost_total is None:
        return 'not priced yet'
    return f'{report.cost_total}{" (at list price)" if report.cost_estimated else ""}'


def service_debug_lines(report: ServiceDebugReport) -> list[str]:
    """The report as lines, shared by `--text` and by the view.

    One list for both surfaces so the two cannot drift, and one place that decides
    what a gap reads like: every number is the service's, and every section it does
    not publish is named as unmeasured instead of painted as zero.
    """
    lines = ['Agent debug — attached to the service']
    if report.session:
        lines.append(f'  session: {report.session}')
    if report.agent:
        lines.append(f'  agent: {report.agent}')
    lines.extend([f'  model: {report.model or NOT_REPORTED}  effort: {report.effort or NOT_REPORTED}', ''])

    lines.append('Context (projected by the service)')
    if not report.context_read:
        lines.append('  the service did not answer /api/context: nothing is measured here')
    else:
        lines.append(f'  window: {_tokens(report.window)}')
        lines.append(f'  used: {_used(report)}')
        lines.append(f'  last context (reported input + output): {_tokens(report.measured)}')
        lines.append(f'  projected total: {_tokens(report.projected)}')
        if report.budget:
            lines.append(f'  budget per request: {_budget(report.budget)}')
        lines.extend(['', 'Cost (reported by the service)'])
        lines.append(f'  total: {_cost(report)}')
        requests = NOT_REPORTED if report.cost_requests is None else f'{report.cost_requests}'
        lines.append(f'  requests: {requests}')

    lines.extend(['', 'What this process does not measure'])
    lines.append(f'  {NOT_MEASURED}')
    for name, what in NOT_MEASURED_SECTIONS:
        lines.append(f'    {name}: {what}')
    return lines


def render_service_debug_report(report: ServiceDebugReport) -> str:
    """The attached report as plain text, for `/debug --text` and for copying."""
    return '\n'.join(service_debug_lines(report))


def build_service_debug_header(report: ServiceDebugReport) -> Text:
    """Fixed lines of the attached view: whose session, on what, how full."""
    text = Text()
    text.append(' DEBUG ', style='bold black on yellow')
    text.append('  Attached to the service', style='bold')
    text.append('   alt+d to close\n', style='dim')
    window = '?' if report.window is None else f'{report.window:,}'
    text.append(f'  model {report.model or NOT_REPORTED}  effort {report.effort or NOT_REPORTED}  window {window}\n')
    if report.session:
        text.append(f'  session {report.session}')
        if report.agent:
            text.append(f' · {report.agent}')
        text.append('\n')
    text.append('  context ')
    text.append(_used(report) if report.context_read else NOT_REPORTED)
    text.append('  cost ')
    text.append(_cost(report) if report.context_read else NOT_REPORTED)
    return text


def build_service_debug_body(report: ServiceDebugReport) -> Text:
    """The report's own lines: every number from the service, every gap named."""
    return Text('\n'.join(service_debug_lines(report)))


def _whole(value: object) -> int | None:
    """A JSON integer, or ``None``. Booleans are not numbers here."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _ratio(value: object) -> float | None:
    """A JSON number, or ``None``. Booleans are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class DebugMixin:
    """Toggles between the chat and the agent debug view.

    In process it expects the app to define ``_history``, ``_meter`` and
    ``_context_capability``; attached, ``_client`` and ``_service_state``, and it
    reads the service instead of measuring a run this process does not have.
    """

    _history: list[ModelMessage]
    _meter: Any
    _context_capability: Any
    _client: Any
    _service_state: dict[str, Any] | None
    _service_debug_context: dict[str, Any] | None
    debug_visible: bool
    _debug_timer: Any
    _debug_pending: list[ModelMessage] | None

    # --- toggle ----------------------------------------------------------

    def toggle_debug(self) -> None:
        """Shows or hides the debug view, replacing the chat."""
        self.set_debug_visible(not self.debug_visible)

    def set_debug_visible(self, visible: bool) -> None:
        """Sets the debug view state and redraws."""
        self.debug_visible = visible
        try:
            view = self.query_one(AgentDebugView)
            chat = self.query_one(ChatLog)
            prompt = self.query_one(PromptInput)
        except NoMatches:
            return
        view.display = visible
        # It replaces, it does not coexist: the space is the same and the view is the same
        # conversation told from the other side. The input stays active and
        # focused, on purpose: the view refreshes on every step of the turn, so
        # you can send a prompt and watch live what context and cache it costs.
        chat.display = not visible
        if visible:
            self.refresh_debug(immediate=True)
        else:
            self._cancel_debug_refresh()
        prompt.focus()

    # --- reporte ---------------------------------------------------------

    def debug_from_service(self) -> bool:
        """Whether the report comes from the service instead of from this process."""
        return getattr(self, '_client', None) is not None

    def service_debug_report(self) -> ServiceDebugReport:
        """The report of an attached terminal, from the projections it already holds."""
        state = getattr(self, '_service_state', None)
        return ServiceDebugReport.from_projection(
            getattr(self, '_service_debug_context', None), state if isinstance(state, Mapping) else {}
        )

    async def read_service_debug_report(self) -> ServiceDebugReport:
        """Reads `/api/context` and turns it, with the state, into the report.

        A route that does not answer is not a session at zero: the report says it did
        not answer and keeps what the state still knows — the model, the session, the
        agent — because those are facts nobody has to re-measure.
        """
        client = getattr(self, '_client', None)
        context: Mapping[str, Any] | None = None
        if client is not None:
            try:
                context = await client.context()
            except ServiceClientError:
                context = None
        self._service_debug_context = dict(context) if context is not None else None
        return self.service_debug_report()

    @work(exclusive=True, group=SERVICE_DEBUG)
    async def refresh_service_debug(self) -> None:
        """Repaints the attached view from the service's projection.

        The turn's own events are the refresh — `turn_started`, `tool_result`,
        `state_changed` — because there is no run here to react to. The exclusive
        group coalesces a burst of them into one read, and the newest numbers paint.
        """
        if not self.debug_visible or not self.debug_from_service():
            return
        report = await self.read_service_debug_report()
        self._paint_service_debug(report)

    def _paint_service_debug(self, report: ServiceDebugReport) -> None:
        """Pushes the attached report into the same widget the in-process one uses."""
        try:
            view = self.query_one(AgentDebugView)
            header = view.query_one('#debug-header', Static)
            body = view.query_one('#debug-body', Static)
        except NoMatches:
            return
        header.update(build_service_debug_header(report))
        body.update(build_service_debug_body(report))

    def debug_report(self, messages: Sequence[ModelMessage] | None = None) -> DebugReport:
        """Builds the debug report with the current log and the next request projection."""
        history = list(self._history if messages is None else messages)
        capability = self._context_capability
        request: RequestSnapshot | None = None
        view_result = None
        if capability is not None:
            request = capability.last_request
            try:
                # The next request's view, not the previous one's: what the
                # agent is going to receive with the current history.
                view_result = capability.project(history)
            except Exception:  # noqa: BLE001 - diagnostics never break the UI
                view_result = capability.last_view
        return build_debug_report(
            history,
            snapshot=self._meter.snapshot(history),
            request=request or _EMPTY_REQUEST,
            view_result=view_result,
            effort=self.effort_in_effect(),
            model=self._model_snapshot().label,
        )

    def refresh_debug(self, messages: Sequence[ModelMessage] | None = None, *, immediate: bool = False) -> None:
        """Requests a repaint of the debug view. It does nothing if it is closed.

        Without ``immediate`` the repaint is coalesced: a single one is scheduled per
        window and the latest history wins. Attached there is no history to hand
        over — the read of the service's meter is the repaint — so the pending
        history is cleared and the worker does the rest.
        """
        if not self.debug_visible:
            return
        if self.debug_from_service():
            self._debug_pending = None
        else:
            run = getattr(self, '_current_run', None)
            current = run.all_messages() if messages is None and run is not None else self._history
            self._debug_pending = list(current if messages is None else messages)
        if immediate:
            self._flush_debug()
        elif self._debug_timer is None:
            self._debug_timer = self.set_timer(_REFRESH_DELAY, self._flush_debug)

    def _cancel_debug_refresh(self) -> None:
        """Discards a scheduled repaint: the view is no longer shown."""
        if self._debug_timer is not None:
            self._debug_timer.stop()
            self._debug_timer = None
        self._debug_pending = None

    def _flush_debug(self) -> None:
        """Paints the pending report. It is the coalescing timer callback."""
        self._debug_timer = None
        pending = self._debug_pending
        self._debug_pending = None
        if not self.debug_visible:
            return
        if self.debug_from_service():
            self.refresh_service_debug()
            return
        try:
            view = self.query_one(AgentDebugView)
        except NoMatches:
            return
        try:
            report = self.debug_report(pending)
        except Exception:  # noqa: BLE001 - not even diagnostics bring the app down
            return
        view.update_report(report)

    # --- commands ---------------------------------------------------------

    async def show_debug_text(self) -> str:
        """Plain-text report, for `/debug --text` and for copying."""
        if self.debug_from_service():
            return render_service_debug_report(await self.read_service_debug_report())

        from src.engine.context.meter.debug_render import render_debug_report

        report = render_debug_report(self.debug_report())
        timings = getattr(self, '_turn_timings', None)
        return report if timings is None else f'{report}\n\n{timings.render()}'
