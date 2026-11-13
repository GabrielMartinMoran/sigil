"""Side panel: active model, context budget, loaded skills and MCP state."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic_ai.messages import ModelMessage
from textual import work
from textual.app import ScreenStackError
from textual.css.query import NoMatches

from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.meter.cost import SessionCost, session_cost
from src.engine.roadmap.models.todo_item import TodoItem
from src.engine.skills.loaded import loaded_skills
from src.ui.service_client import ServiceClientError
from src.ui.widgets.sidebar_panel import ContextState, MCPServerState, ModelState, SidebarPanel

SERVICE_CONTEXT = 'sidebar-context'
"""The worker that reads `/api/context`.

It lives in a group of its own so a context refresh never cancels the MCP probe
or a turn. Exclusive: a second request replaces the first, and the newest
measurement is the one that wins.
"""


class SidebarMixin:
    """Keeps the side panel up to date.

    In process it expects the app to define ``viewed_skills``, ``_mcp_manager``,
    ``_meter`` and ``_history``. Attached it reads the service projection in
    ``_service_state`` and the meter over HTTP instead, and touches none of them:
    the process that owns the session is the only one that can measure it.
    """

    _mcp_manager: object | None
    _meter: ContextMeter
    _history: list[ModelMessage]

    def refresh_sidebar(self) -> None:
        """Model, context budget, skills, MCP and the plan.

        Everything here belongs to the agent on screen, not to the session: a
        subagent has its own loaded skills, its own context and its own bill, and
        the panel shows the one the user is reading. That is also why the panel
        carries the name of the agent: two columns that look alike for a parent and
        its child would be unreadable.

        Attached, the local collaborators are empty by construction — a terminal
        that owns no session has no history, no meter and no MCP manager — so the
        projection is the only source that is not a guess.
        """
        panel = self._sidebar_panel()
        if panel is None:
            return
        if getattr(self, '_client', None) is not None:
            self.paint_service_sidebar(panel)
            self.refresh_service_context()
            return
        state = getattr(self, 'model_state', None)
        panel.update_model(state() if callable(state) else None)
        panel.update_context(self.context_state())
        viewer = getattr(self, 'viewed_skills', None)
        names = viewer() if callable(viewer) else loaded_skills(self._history)
        panel.update_skills(names)
        self._update_agent_label(panel)

    def _sidebar_panel(self) -> SidebarPanel | None:
        """The panel, or ``None`` before it is composed."""
        try:
            return self.query_one(SidebarPanel)
        except NoMatches, ScreenStackError:
            return None

    def _update_agent_label(self, panel: SidebarPanel) -> None:
        label = getattr(self, 'viewed_agent_label', None)
        if callable(label):
            panel.update_agent(label())

    # --- the projection ----------------------------------------------------

    def paint_service_sidebar(self, panel: SidebarPanel | None = None) -> None:
        """Paints every section from the service's projection.

        A section the projection does not carry is marked unknown, never painted
        as an empty list or a zero: `none yet`, `No tasks yet.` and `not
        configured` are claims about a session this process does not own.

        The local `RoadmapStore` is not consulted even when one exists: it reads
        the shared SQLite file once, in its constructor, and nothing here can see
        the service's writes afterwards. Two copies of one plan where one is a
        frozen photograph is worse than one projection.
        """
        state = getattr(self, '_service_state', None)
        if not state:
            return
        target = panel if panel is not None else self._sidebar_panel()
        if target is None:
            return
        target.update_model(_model_from_state(state), reported='model' in state)
        target.update_skills(_skills_from_state(state), reported=_skills_reported(state))
        target.update_mcp(_servers_from_state(state), reported=_mcp_reported(state))
        target.update_roadmap(_roadmap_from_state(state))
        self._update_agent_label(target)

    def refresh_skills(self) -> None:
        """Repaints the skills section only when the set on screen changed.

        It is called after every finished tool call, so it has to be cheap and quiet:
        the set is derived from the transcript, and comparing it to the one painted is
        the whole check. A client that instead watched for a particular tool name would
        know a detail of the domain it has no business knowing.

        Attached, there is no transcript here to derive it from: the state carries
        the set the service derived, and painting it is the whole refresh.
        """
        if getattr(self, '_client', None) is not None:
            self.paint_service_sidebar()
            return
        panel = self._sidebar_panel()
        if panel is None:
            return
        viewer = getattr(self, 'viewed_skills', None)
        names = viewer() if callable(viewer) else loaded_skills(self._history)
        if names == getattr(self, '_painted_skills', None):
            return
        self._painted_skills = names
        panel.update_skills(names)

    def refresh_context(self, messages: Sequence[ModelMessage] | None = None) -> None:
        """Repaints only the context section.

        It is the hot path: a turn calls it on every checkpoint, and the model and
        the skills did not move. During a turn it is given the run history, which
        runs ahead of `self._history`.

        Attached, the history handed in belongs to a process that is not running
        the turn, so the number is re-read from the service's meter instead.
        """
        if getattr(self, '_client', None) is not None:
            self.refresh_service_context()
            return
        panel = self._sidebar_panel()
        if panel is None:
            return
        panel.update_context(self.context_state(messages))

    @work(exclusive=True, group=SERVICE_CONTEXT)
    async def refresh_service_context(self) -> None:
        """Reads how full the window is from the service's own meter.

        The terminal has no history in attached mode; a percentage computed here
        would be a second measurement of a conversation it does not own. The
        newest answer wins: a checkpoint during a turn can schedule this several
        times, and the exclusive group drops the stale request.
        """
        client = getattr(self, '_client', None)
        if client is None:
            return
        read = getattr(client, 'context', None)
        if not callable(read):
            # A double that does not carry the meter route has no number to
            # give. The line keeps what it had; inventing a zero would be worse.
            return
        try:
            payload = await read()
        except ServiceClientError:
            # The last known number is kept: a failed read is not a measurement
            # of zero, and the stream will ask again on the next state change.
            return
        panel = self._sidebar_panel()
        if panel is not None:
            panel.update_context(context_state_from_service(payload))

    def context_state(self, messages: Sequence[ModelMessage] | None = None) -> ContextState:
        """How full the window is and what the session has cost, from the meter.

        The **projected** number, not the measured one: the panel answers whether
        the next request fits, and the measured value describes the previous one.

        With no history there is nothing measured and nothing estimated, so the
        real usage is zero. Reporting it as unknown would paint `?` on every cold
        start, which is the moment the panel is read most. The bill is measured on
        the same history and travels with it: a zero there means nothing was sent
        yet, which is a fact, not a gap.
        """
        history = self.viewed_history() if messages is None else messages
        snapshot = self._meter.snapshot(history)
        cost = session_cost(history)
        if snapshot.projected is None and snapshot.window:
            return ContextState(percent=0.0, used=0, window=snapshot.window, cost=cost)
        return ContextState(
            percent=snapshot.projected_percent,
            used=snapshot.projected,
            window=snapshot.window,
            cost=cost,
        )

    @work(exclusive=True, group='mcp')
    async def refresh_mcp_status(self) -> None:
        """Real MCP server count: it connects and updates the panel.

        It runs in a worker so as not to block startup: bringing the servers
        up can take time. If it fails, the panel keeps what it already knew.

        It has a worker group of its own because it shares the startup with
        ``resolve_model_capabilities``, and both used to live in the default
        group: the second exclusive worker cancelled the first, so the MCP probe
        never ran and the section never appeared. A turn is also exclusive in
        that group, which would have cancelled it mid-connection.

        The manager may still be loading (see ``boot_mcp``): ``describe`` waits for
        it, so the panel ends up with the real state either way.
        """
        if getattr(self, '_client', None) is not None:
            # The service owns the probe. Connecting here as well would open a
            # second set of subprocesses to answer a question already answered,
            # and the projection is the answer the panel is supposed to show.
            self.paint_service_sidebar()
            return
        if self._mcp_manager is None or not self._mcp_manager:
            return
        try:
            statuses = await self._mcp_manager.describe(connect=True)
        except Exception:  # noqa: BLE001 - diagnostics never break the app
            return
        self.apply_mcp_status(statuses)

    def apply_mcp_status(self, statuses: list[object]) -> None:
        """Dumps the MCP state into the side panel."""
        servers = [MCPServerState(name=status.name, ok=not status.error) for status in statuses if status.enabled]
        panel = self._sidebar_panel()
        if panel is not None:
            panel.update_mcp(servers)

    async def close_mcp_servers(self) -> None:
        """Releases the MCP sessions the probe left open, concurrently.

        The probe connects with ``keep_alive`` on, so every server keeps its
        subprocess and its session after the panel is painted. Nothing closes them
        on the way out, and the event loop then waits each one out — which is what
        made quitting take seconds. Closing here is bounded by the grace period
        ``src.engine.mcp.shutdown`` installs, and it runs once per quit.
        """
        manager = self._mcp_manager
        if manager is None or not manager:
            return
        close = getattr(manager, 'aclose', None)
        if close is None:
            return
        try:
            await close()
        except Exception:  # noqa: BLE001 - a quit never fails over a diagnostic
            return


def context_state_from_service(payload: Mapping[str, Any]) -> ContextState:
    """The panel's context line, from the projection of the service's meter.

    ``percent`` of ``0.0`` is a real measurement — a cold session with a known
    window — and is painted ``0%``. Only a value the route did not send becomes
    ``None`` and paints ``?``; the two are different claims.

    One limitation travels here: `/api/context` projects ``total``, ``approximate``
    and ``requests``, not the ``unpriced``/``unmeasured`` counts. A partial total
    (the ``+`` mark the in-process path can show) therefore cannot be rebuilt, and
    is not invented. The route is the place to widen if that mark is wanted.
    """
    percentage = _ratio(payload.get('percent'))
    used = _whole(payload.get('used'))
    window = _whole(payload.get('window'))
    cost_view = payload.get('cost')
    cost_view = cost_view if isinstance(cost_view, Mapping) else {}
    total = _amount(cost_view.get('total'))
    requests = _whole(cost_view.get('requests')) or 0
    approximate = bool(cost_view.get('approximate'))
    cost = SessionCost(
        total=total,
        reported=0 if approximate else requests,
        estimated=requests if approximate else 0,
        unpriced=requests if total is None and requests else 0,
    )
    return ContextState(percent=percentage, used=used, window=window, cost=cost)


def _model_from_state(state: Mapping[str, Any]) -> ModelState | None:
    """The active model as `/api/state` projects it.

    `model_view` carries no auth label, so ``ModelState.auth`` stays empty and the
    panel — which never rendered that field — does not pretend to know it.
    """
    view = state.get('model')
    if not isinstance(view, Mapping) or not view.get('configured'):
        return None
    account = view.get('account')
    account = account if isinstance(account, Mapping) else {}
    return ModelState(
        alias=str(account.get('alias') or ''),
        provider=str(account.get('kind') or ''),
        model=str(view.get('model') or ''),
        effort=str(view.get('thinking') or 'default'),
    )


def _skills_reported(state: Mapping[str, Any]) -> bool:
    """Whether the projection asserted the loaded-skill list."""
    return isinstance(state.get('skills'), list)


def _skills_from_state(state: Mapping[str, Any]) -> tuple[str, ...]:
    names = state.get('skills')
    if not isinstance(names, list):
        return ()
    return tuple(str(name) for name in names)


def _mcp_reported(state: Mapping[str, Any]) -> bool:
    """Whether the projection has an MCP answer yet.

    ``loading`` means the service has not opened its layer yet: an empty list
    there is "not measured", not "no servers", so it is painted as unknown.
    """
    view = state.get('mcp')
    return isinstance(view, Mapping) and not view.get('loading')


def _servers_from_state(state: Mapping[str, Any]) -> tuple[MCPServerState, ...]:
    view = state.get('mcp')
    if not isinstance(view, Mapping):
        return ()
    servers = view.get('servers')
    if not isinstance(servers, list):
        return ()
    return tuple(
        MCPServerState(name=str(entry.get('name') or ''), ok=not entry.get('error'))
        for entry in servers
        if isinstance(entry, Mapping) and entry.get('enabled', True)
    )


def _roadmap_from_state(state: Mapping[str, Any]) -> tuple[TodoItem, ...] | None:
    """The plan the service projects, or ``None`` when it did not project one."""
    items = state.get('roadmap')
    if not isinstance(items, list):
        return None
    return _todo_items(items)


def _todo_items(items: list[Any]) -> tuple[TodoItem, ...]:
    """Rows of the projection as tasks, skipping rows no task can be built from."""
    tasks: list[TodoItem] = []
    for entry in items:
        if not isinstance(entry, Mapping):
            continue
        title = entry.get('title')
        if not isinstance(title, str) or not title:
            continue
        data = {key: entry[key] for key in ('id', 'title', 'status', 'notes') if key in entry}
        try:
            tasks.append(TodoItem(**data))
        except ValueError:
            # A status this build does not know is a row it cannot paint; the
            # rest of the plan is not lost because of it.
            continue
    return tuple(tasks)


def _whole(value: object) -> int | None:
    """A JSON integer, or ``None``. Booleans are not numbers here."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _ratio(value: object) -> float | None:
    """A JSON number, or ``None``. Booleans are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _amount(value: object) -> Decimal | None:
    """A non-negative amount from the string the route sends, or ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation, ValueError:
        return None
    return amount if amount.is_finite() and amount >= 0 else None
