"""Shared provider commands test support."""

from __future__ import annotations

from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from tests.support.provider_setup import _service


def _app(tmp_path: Path, service=None, session=None) -> SigilApp:
    store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')
    session = session or store.create_session()
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=session,
        model_service=service or _service(tmp_path),
    )


def _try_render(child):
    """The child's renderable, or ``None`` when it has none."""
    try:
        return child.render()
    except Exception:  # noqa: BLE001 - optional protocol on the widget
        return None


def _texts(app) -> list[str]:
    """Plain text of every chat message, rendered as the widget does."""
    out = []
    for child in app.query_one(ChatLog).children:
        # Some chat children are containers with no renderable of their own.
        rendered = _try_render(child)
        plain = getattr(rendered, 'plain', None)
        if isinstance(plain, str):
            out.append(plain)
    return out


def _submit(app, text: str) -> None:
    """Types a command and submits it, bypassing the command menu.

    `pilot.press` on a string with a space opens the menu and `Enter` then picks
    the highlighted command, which is not what these tests are about.
    """
    field = app.query_one(PromptInput)
    field.value = text
    field.post_message(PromptInput.Submitted(text))


async def _wait_idle(app, pilot, attempts: int = 40) -> None:
    for _ in range(attempts):
        await pilot.pause()
        if not app._turn_active:
            return
