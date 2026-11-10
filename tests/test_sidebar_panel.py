"""Tests for the side panel: model, skills, MCP counts and the roadmap."""

from __future__ import annotations

import asyncio
import tempfile
from decimal import Decimal
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage

from src.engine.context.meter.cost import SessionCost
from src.engine.mcp.config.loader import load_config
from src.engine.mcp.manager.manager import MCPManager
from src.engine.roadmap.models.todo_item import TodoItem
from src.engine.roadmap.store import RoadmapStore
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.sidebar_mixin import SidebarMixin
from src.ui.widgets.sidebar_panel import ContextState, MCPServerState, ModelState, SidebarPanel
from src.ui.widgets.status_bar import StatusBar


def _panel() -> SidebarPanel:
    return SidebarPanel()


def _configured() -> SidebarPanel:
    panel = _panel()
    panel._model = ModelState(alias='work', provider='OpenRouter', model='anthropic/x', effort='high')
    return panel


def test_panel_without_skills_says_so():
    text = _panel()._build().plain
    assert 'Loaded skills' in text
    assert 'none yet' in text


def test_panel_hides_the_context_until_a_model_is_configured():
    """No model, no window, no percentage: the line does not appear at all."""
    panel = _panel()
    panel._context_state = ContextState(percent=12.0, used=24_000, window=200_000)
    assert 'context:' not in panel._build().plain


def test_panel_shows_the_percentage_and_the_compacted_pair():
    panel = _configured()
    panel._context_state = ContextState(percent=66.0, used=132_000, window=200_000)
    text = panel._build().plain
    assert 'context: 66% (132K / 200K)' in text


def test_panel_rounds_the_percentage_to_whole_numbers():
    panel = _configured()
    panel._context_state = ContextState(percent=12.4, used=24_800, window=200_000)
    assert '12%' in panel._build().plain
    assert '12.4%' not in panel._build().plain


def test_panel_shows_a_question_mark_without_a_window():
    panel = _configured()
    panel._context_state = ContextState(percent=None, used=None, window=None)
    text = panel._build().plain
    assert 'context: ?' in text
    # Without a percentage there is no pair to compare against.
    assert '(' not in text


def test_panel_omits_the_pair_when_only_the_window_is_known():
    panel = _configured()
    panel._context_state = ContextState(percent=None, used=None, window=200_000)
    text = panel._build().plain
    assert 'context: ?' in text
    assert '(/ 200K)' not in text
    assert '?' + ' (' not in text


def test_panel_escalates_the_context_colour():
    panel = _configured()

    def colour(percent: float) -> set[str | None]:
        panel._context_state = ContextState(percent=percent, used=int(percent * 2000), window=200_000)
        return {span.style for span in panel._build().spans}

    assert 'green' in colour(41.0)
    assert 'yellow' in colour(75.0)
    assert 'bold red' in colour(93.0)


def test_the_context_is_the_last_line_of_the_model_section():
    """The budget belongs to the window of that model: one heading for it cost two rows."""
    panel = _configured()
    panel._skills = ('clean-code',)
    panel._context_state = ContextState(percent=66.0, used=132_000, window=200_000)
    text = panel._build().plain
    assert 'Context' not in text
    assert text.index('effort: high') < text.index('context: 66%') < text.index('Loaded skills')


def test_the_cost_rides_under_the_context_line():
    """Same history, different unit: how full the window is, and what it cost."""
    panel = _configured()
    panel._context_state = ContextState(
        percent=66.0,
        used=132_000,
        window=200_000,
        cost=SessionCost(total=Decimal('0.043'), reported=3),
    )
    text = panel._build().plain
    assert text.index('context: 66%') < text.index('cost: $0.04') < text.index('Loaded skills')


def test_a_reported_total_carries_no_marks():
    """OpenRouter charges what it reports: the number is an invoice, not a guess."""
    panel = _configured()
    panel._context_state = ContextState(
        percent=10.0, used=20_000, window=200_000, cost=SessionCost(total=Decimal('1.2'), reported=4)
    )
    assert 'cost: $1.20' in panel._build().plain


def test_an_estimated_total_carries_a_tilde():
    """A list price is not a charge: a reseller or a plan can bill something else."""
    panel = _configured()
    panel._context_state = ContextState(
        percent=10.0, used=20_000, window=200_000, cost=SessionCost(total=Decimal('0.043'), estimated=2)
    )
    assert 'cost: ~$0.04' in panel._build().plain


def test_a_partial_total_carries_a_plus():
    """A request nothing could price turns the total into a floor."""
    panel = _configured()
    panel._context_state = ContextState(
        percent=10.0,
        used=20_000,
        window=200_000,
        cost=SessionCost(total=Decimal('0.043'), reported=2, unpriced=1),
    )
    assert 'cost: $0.04+' in panel._build().plain


def test_both_marks_together():
    panel = _configured()
    panel._context_state = ContextState(
        percent=10.0,
        used=20_000,
        window=200_000,
        cost=SessionCost(total=Decimal('0.043'), estimated=2, unpriced=1),
    )
    assert 'cost: ~$0.04+' in panel._build().plain


def test_a_session_that_could_not_be_priced_at_all_shows_a_question_mark():
    panel = _configured()
    panel._context_state = ContextState(
        percent=10.0,
        used=20_000,
        window=200_000,
        cost=SessionCost(total=None, unpriced=2),
    )
    assert 'cost: ?' in panel._build().plain


def test_a_session_with_nothing_sent_costs_zero():
    """A real zero, not `?`: nothing went out, so nothing is unknown."""
    panel = _configured()
    panel._context_state = ContextState(percent=0.0, used=0, window=200_000, cost=SessionCost(total=Decimal(0)))
    assert 'cost: $0' in panel._build().plain


def test_the_cost_is_hidden_until_a_model_is_configured():
    """A bill for a window that does not exist yet is a number that means nothing."""
    panel = _panel()
    panel._context_state = ContextState(
        percent=12.0,
        used=24_000,
        window=200_000,
        cost=SessionCost(total=Decimal('0.5'), reported=1),
    )
    assert 'cost:' not in panel._build().plain


def test_the_roadmap_is_the_last_section_of_the_panel(tmp_path):
    """One panel: the plan closes the same list the model, skills and MCP are in."""
    # No execution: a memory-only store, which is all the panel needs to paint.
    store = RoadmapStore(base_dir=tmp_path)
    store.set_items([TodoItem(title='Ship it', status='in_progress')])
    panel = SidebarPanel(store)
    panel._skills = ('clean-code',)
    panel._mcp_known = True
    panel._servers = (MCPServerState(name='github', ok=True),)
    text = panel._build().plain
    assert text.index('Model') < text.index('Loaded skills') < text.index('MCP') < text.index('Roadmap')
    assert text.index('Roadmap') < text.index('Ship it')


def test_the_panel_without_a_store_does_not_announce_a_roadmap():
    """No store, no plan: the section stays away instead of reading `No roadmap.`"""
    assert 'Roadmap' not in _panel()._build().plain


def test_the_workspace_is_not_repeated_in_the_panel():
    """The status bar carries it; a second copy would spend four rows on the same fact."""
    assert 'Workspace' not in _panel()._build().plain


def test_status_bar_starts_at_rest():
    bar = StatusBar()
    assert bar.plain() == '● Ready'


def test_status_bar_carries_the_workspace_badge():
    from src.ui.widgets.status_bar import WorkspaceBadge

    bar = StatusBar()
    bar.set_workspace(WorkspaceBadge(label='fix-login', branch='sigil/fix-login', is_worktree=True))
    text = bar.plain()
    assert 'fix-login' in text
    assert 'sigil/fix-login' in text


def test_panel_lists_loaded_skills():
    panel = _panel()
    panel._skills = ('clean-code', 'vscode')
    text = panel._build().plain
    assert 'clean-code' in text
    assert 'vscode' in text


def test_panel_hides_mcp_section_without_servers():
    panel = _panel()
    panel._mcp_known = True
    assert 'MCP' not in panel._build().plain


def test_panel_shows_count_inline_with_title():
    panel = _panel()
    panel._mcp_known = True
    panel._servers = (
        MCPServerState(name='github', ok=True),
        MCPServerState(name='playwright', ok=True),
    )
    text = panel._build().plain
    assert 'MCP (2)' in text
    assert 'github' in text
    assert 'playwright' in text


def test_panel_marks_failures_with_a_cross():
    panel = _panel()
    panel._mcp_known = True
    panel._servers = (
        MCPServerState(name='github', ok=True),
        MCPServerState(name='broken', ok=False),
    )
    text = panel._build().plain
    assert 'MCP (1, 1 ✗)' in text
    assert ' ✗ broken' in text
    assert ' • github' in text


def test_panel_lists_only_failures_when_none_ok():
    panel = _panel()
    panel._mcp_known = True
    panel._servers = (MCPServerState(name='broken', ok=False),)
    text = panel._build().plain
    assert 'MCP (0, 1 ✗)' in text
    assert '•' not in text


def test_mcp_and_skills_share_the_same_bullet():
    """Both lists are the same kind of list, so they hang off the same mark.

    A wider glyph on one of them reads as a different level of the hierarchy, and
    the two sections sit three rows apart in a 34-column panel.
    """
    panel = _panel()
    panel._skills = ('clean-code',)
    panel._mcp_known = True
    panel._servers = (MCPServerState(name='github', ok=True),)
    lines = panel._build().plain.splitlines()

    assert ' • clean-code' in lines
    assert ' • github' in lines


def test_panel_colors_ok_green_and_failures_red():
    panel = _panel()
    panel._mcp_known = True
    panel._servers = (
        MCPServerState(name='github', ok=True),
        MCPServerState(name='broken', ok=False),
    )
    styles = {span.style for span in panel._build().spans}
    assert 'green' in styles
    assert 'bold red' in styles


def test_mcp_counts_ignores_disabled_servers(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text(
        '{"mcp_servers": {"on": {"command": "npx"}, "off": {"command": "npx", "enabled": false}}}',
        encoding='utf-8',
    )
    manager = MCPManager(load_config([config], environ={}).servers)
    ok, failed = manager.counts()
    assert ok == 1
    assert failed == 0


def test_mcp_counts_reports_broken_servers(tmp_path):
    config = tmp_path / 'config.json'
    # An invalid transport makes the toolset construction fail.
    config.write_text('{"mcp_servers": {"broken": {"url": "not-a-url"}}}', encoding='utf-8')
    manager = MCPManager(load_config([config], environ={}).servers)
    ok, failed = manager.counts()
    assert ok == 0
    assert failed == 1


def test_the_panel_reads_the_bill_from_the_session_history():
    """The whole seam: history → `context_state` → panel.

    The panel test above pins the rendering; this one pins that the number reaches
    it, which is the part a refactor of `context_state` would break.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.model_state = lambda: ModelState(alias='work', provider='OpenRouter', model='anthropic/x')
                app._history = [
                    ModelRequest(parts=[UserPromptPart(content='hi')]),
                    ModelResponse(
                        parts=[TextPart(content='ok')],
                        usage=RequestUsage(input_tokens=1_000, output_tokens=100),
                        model_name='claude-sonnet-4-5',
                        provider_details={'cost': 0.042},
                    ),
                ]
                app.refresh_sidebar()
                await pilot.pause()
                assert 'cost: $0.04' in app.query_one(SidebarPanel).text

    asyncio.run(inner())


def test_sidebar_reflects_loaded_skills():
    """The panel reads the set from the transcript on screen, not from a registry."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            agent = Agent(TestModel(custom_output_text='ok'), instructions='x')
            app = SigilApp(agent=agent, store=store, session=session)

            async with app.run_test() as pilot:
                await pilot.pause()
                panel = app.query_one(SidebarPanel)
                assert 'none yet' in panel._build().plain

                # A load in the history of the execution on screen.
                app._history = [
                    ModelResponse(
                        parts=[ToolCallPart(tool_name='load_skill', args={'name': 'clean-code'}, tool_call_id='c1')]
                    ),
                    ModelRequest(parts=[ToolReturnPart(tool_name='load_skill', content='ok', tool_call_id='c1')]),
                ]
                app.refresh_sidebar()
                await pilot.pause()
                assert 'clean-code' in panel._build().plain

    asyncio.run(inner())


def test_the_mcp_section_survives_the_startup_capability_fetch(tmp_path):
    """The regression: the probe and the capability fetch are both exclusive workers.

    They shared the default worker group, so `resolve_model_capabilities` cancelled
    `refresh_mcp_status` on startup. The probe never ran, `update_mcp` was never
    called and the section did not exist — for every session, with every config. It
    looked exactly like "the MCP section disappeared".
    """
    config = tmp_path / 'config.json'
    config.write_text('{"mcp_servers": {"ghost": {"command": "definitely-not-a-binary"}}}', encoding='utf-8')
    manager = MCPManager(load_config([config], environ={}).servers)

    async def inner():
        store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')
        app = SigilApp(
            agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
            store=store,
            session=store.create_session(),
            mcp_manager=manager,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # The probe connects to a binary that does not exist, which is a fast
            # failure: a second is plenty even on a loaded machine.
            for _ in range(50):
                await pilot.pause()
                await asyncio.sleep(0.05)
                if app.query_one(SidebarPanel)._mcp_known:
                    break
            text = app.query_one(SidebarPanel).text
            assert 'MCP (0, 1 ✗)' in text
            assert 'ghost' in text

    asyncio.run(inner())


def test_the_mcp_probe_does_not_share_the_turn_worker_group():
    """A turn is `exclusive` in the default group; the probe must not be in it.

    The group is asserted on the worker the decorator returns, because that is where
    Textual keeps it. A test that only checked the panel would pass again the day
    someone moved the probe back into the default group, as long as no turn happened
    to be starting at the same time.
    """
    assert SidebarMixin.__dict__['refresh_mcp_status'].__wrapped__ is not None

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=store.create_session(),
            )
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                assert app.refresh_mcp_status().group == 'mcp'

    asyncio.run(inner())


def test_status_bar_displays_agent_name_without_prefix():
    bar = StatusBar()
    bar.set_agent('wizard')
    assert bar.plain().startswith('wizard')
    assert 'agent wizard' not in bar.plain()


def test_project_name_appears_at_the_start_of_status_bar():
    bar = StatusBar()
    bar.set_project('sigil')
    bar.set_agent('wizard')
    assert bar.plain().startswith('sigil  ·  wizard')
    bar.show('Press Esc to cancel')
    assert bar.plain().startswith('sigil  ·  wizard')
