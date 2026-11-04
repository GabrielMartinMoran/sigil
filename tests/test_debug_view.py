"""Tests de la vista de debug en la app: `alt+d` reemplaza el chat."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

os.environ.setdefault('LLM_MODEL', 'test/model')
os.environ.setdefault('OPENROUTER_API_KEY', 'test-key')

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import src.ui.app as appmod
from src.engine.context.capability import ContextViewCapability
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.strategy.windowed import WindowedStrategy
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.debug_view import AgentDebugView, build_debug_body, build_debug_header
from src.ui.widgets.prompt_input import PromptInput


async def _fake_fetch(model_id):
    return None


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
    """Texto plano de la cabecera fija de la vista."""
    return app.query_one(AgentDebugView)._header.render().plain


def _report_text(app: SigilApp) -> str:
    """El cuerpo de la vista se pinta como tablas: para asertar, se renderiza el reporte."""
    from src.engine.context.meter.debug_render import render_debug_report

    report = app.query_one(AgentDebugView).last_report
    assert report is not None
    return render_debug_report(report)


def _chat_text(app: SigilApp) -> str:
    chat = app.query_one(ChatLog)
    return '\n'.join(getattr(child.render(), 'plain', '') for child in chat.children if callable(child.render))


def test_alt_d_replaces_chat_with_debug_view(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

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
                # El input sigue usable: se puede promptear y ver el costo en vivo.
                assert app.query_one(PromptInput).disabled is False

                await pilot.press('alt+d')
                await pilot.pause()

                assert app.debug_visible is False
                assert view.display is False
                assert chat.display is True
                assert app.query_one(PromptInput).disabled is False

    asyncio.run(inner())


def test_debug_view_renders_agent_side_sections(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'o', 'l', 'a')
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
                assert 'hola' in body
                assert 'Prompt cache' in body

    asyncio.run(inner())


def test_debug_command_toggles_and_text_mode(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

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
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

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
    """Sin ningún request corrido, la vista se pinta igual (sin datos de tools)."""
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

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
                # Sin request capturado, los ceros de instrucciones/tools no se
                # muestran como si fueran una medición.
                assert 'no request captured' in body

    asyncio.run(inner())


def test_refresh_is_coalesced_while_streaming(monkeypatch):
    """Varios refrescos en ráfaga producen un solo repintado."""
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

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
                # Un solo timer para toda la ráfaga.
                assert app._debug_timer is not None
                await pilot.pause(0.3)

                assert paints == 1
                assert app._debug_timer is None

    asyncio.run(inner())


def test_closing_cancels_a_pending_refresh(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

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
    """Los builders del widget son puros: no necesitan app montada."""
    from src.engine.context.meter.debug import build_debug_report
    from src.engine.context.meter.request import snapshot_request

    meter = ContextMeter(window=1_000)
    report = build_debug_report([], snapshot=meter.snapshot([]), request=snapshot_request(None), model='m')
    assert 'DEBUG' in build_debug_header(report).plain
    assert build_debug_body(report) is not None
