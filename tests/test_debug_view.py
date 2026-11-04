"""Tests for the debug view in the app: `alt+d` replaces the chat."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.context.capability import ContextViewCapability
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.strategy.windowed import WindowedStrategy
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.debug_view import AgentDebugView, build_debug_body, build_debug_header
from src.ui.widgets.prompt_input import PromptInput


def _make_app(tmp: str) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    session = store.create_session()
    meter = ContextMeter(window=200_000)
    capability = ContextViewCapability(strategy=WindowedStrategy(), meter=meter)
    agent = Agent(TestModel(custom_output_text='ok'), instructions='x')
    return SigilApp(
        agent=agent,
        store=store,
        session=session,
        meter=meter,
        context_capability=capability,
    )


def _header_text(app: SigilApp) -> str:
    """Plain text of the fixed header of the view."""
    return app.query_one(AgentDebugView)._header.render().plain


def _report_text(app: SigilApp) -> str:
    """The view body is painted as tables: to assert, the report is rendered."""
    from src.engine.context.meter.debug_render import render_debug_report

    report = app.query_one(AgentDebugView).last_report
    assert report is not None
    return render_debug_report(report)


def _chat_text(app: SigilApp) -> str:
    chat = app.query_one(ChatLog)
    return '\n'.join(getattr(child.render(), 'plain', '') for child in chat.children if callable(child.render))


def test_alt_d_replaces_chat_with_debug_view(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                view = app.query_one(AgentDebugView)
                assert view.display is False

                await pilot.press('alt+d')
                await pilot.pause()

                assert app.debug_visible is True
                assert view.display is True
                # Reemplaza: no conviven.
                assert chat.display is False
                # The input stays usable: you can prompt and watch the cost live.
                assert app.query_one(PromptInput).disabled is False

                await pilot.press('alt+d')
                await pilot.pause()

                assert app.debug_visible is False
                assert view.display is False
                assert chat.display is True
                assert app.query_one(PromptInput).disabled is False

    asyncio.run(inner())


def test_debug_view_renders_agent_side_sections(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'e', 'l', 'l', 'o')
                await pilot.press('enter')
                for _ in range(40):
                    await pilot.pause()
                    if not app.query_one(PromptInput).disabled:
                        break

                await pilot.press('alt+d')
                await pilot.pause()

                header = _header_text(app)
                assert 'DEBUG' in header
                assert 'agent side of the conversation' in header
                assert 'log' in header and 'cache' in header

                body = _report_text(app)
                assert 'Fixed cost per request' in body
                assert 'Agent view' in body
                assert 'Session log' in body
                assert 'hello' in body
                assert 'Prompt cache' in body

    asyncio.run(inner())


def test_debug_command_toggles_and_text_mode(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            async with app.run_test() as pilot:
                await pilot.pause()

                await pilot.press('/')
                for char in 'debug':
                    await pilot.press(char)
                await pilot.press('enter')
                await pilot.pause()

                assert app.debug_visible is True
                assert app.query_one(AgentDebugView).display is True

                # `--text` vuelca el reporte al chat.
                await app.handle_command('/debug', '--text')
                await pilot.pause()

                assert 'Agent debug' in _chat_text(app)
                assert app.debug_visible is True

    asyncio.run(inner())


def test_debug_command_rejects_bad_argument(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                await app.handle_command('/debug', 'nonsense')
                await pilot.pause()
                assert 'Usage: /debug' in _chat_text(app)
                assert app.debug_visible is False

    asyncio.run(inner())


def test_report_without_request_does_not_raise(monkeypatch):
    """With no request run, the view paints anyway (no tool data)."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                app.set_debug_visible(True)
                await pilot.pause()
                body = _report_text(app)
                assert 'Fixed cost per request' in body
                assert 'no usage reported yet' in body
                assert '(empty session)' in body
                # The zeros for instructions/tools are not shown as if they
                # were a measurement.
                assert 'no request captured' in body

    asyncio.run(inner())


def test_refresh_is_coalesced_while_streaming(monkeypatch):
    """Several refreshes in a burst produce a single repaint."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                app.set_debug_visible(True)
                await pilot.pause()

                paints = 0
                original = app._flush_debug

                def counting_flush():
                    nonlocal paints
                    paints += 1
                    original()

                monkeypatch.setattr(app, '_flush_debug', counting_flush)

                for _ in range(5):
                    app.refresh_debug()
                # A single timer for the whole burst.
                assert app._debug_timer is not None
                await pilot.pause(0.3)

                assert paints == 1
                assert app._debug_timer is None

    asyncio.run(inner())


def test_closing_cancels_a_pending_refresh(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                app.set_debug_visible(True)
                await pilot.pause()

                app.refresh_debug()
                assert app._debug_timer is not None

                app.set_debug_visible(False)
                await pilot.pause()
                assert app._debug_timer is None
                assert app._debug_pending is None

    asyncio.run(inner())


def test_header_and_body_are_plain_renderables():
    """The widget builders are pure: they do not need a mounted app."""
    from src.engine.context.meter.debug import build_debug_report
    from src.engine.context.meter.request import snapshot_request

    meter = ContextMeter(window=1_000)
    report = build_debug_report([], snapshot=meter.snapshot([]), request=snapshot_request(None), model='m')
    assert 'DEBUG' in build_debug_header(report).plain
    assert build_debug_body(report) is not None
