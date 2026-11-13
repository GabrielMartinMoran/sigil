"""The selected row of a list stays readable, in every colour the row paints.

The cursor background used to be the theme's accent, and this palette spends its accent
on running work, on the worktree badge and on tool titles. A row painted in those
colours disappeared inside its own selection: in `sigil` the accent on the accent is a
contrast ratio of 1.00, so the branch of the selected session in `/load` was there and
unreadable. These tests read the painted strips — the colours a reader actually gets —
and fail if a span of a selected row drops under a readable ratio.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from textual.color import Color

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.tree.store import AgentTreeStore
from src.engine.sessions.catalog import CatalogEntry
from src.engine.sessions.session_store import Session, SessionStore
from src.engine.workspace.models.worktree import Workspace
from src.ui.app import SigilApp
from src.ui.widgets.agent_tree_screen import AgentTreeScreen
from src.ui.widgets.session_picker import SessionPicker

MIN_CONTRAST = 3.0
"""What a secondary column of a row has to reach to be read, not just seen."""

CHROME = frozenset('▊▎█│─ ')
"""Scroll bars and rules: they are decoration, not text."""


def _luminance(color: Color) -> float:
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    triplet = color.triplet
    assert triplet is not None
    red, green, blue = triplet
    return 0.2126 * channel(red / 255) + 0.7152 * channel(green / 255) + 0.0722 * channel(blue / 255)


def _contrast(one: Color, other: Color) -> float:
    higher, lower = sorted((_luminance(one), _luminance(other)), reverse=True)
    return (higher + 0.05) / (lower + 0.05)


def _spans(app: SigilApp, needle: str) -> list[tuple[str, float]]:
    """Every painted text span of the line that contains ``needle``, with its ratio."""
    assert app.screen is not None
    for strip in app.screen._compositor.render_strips():
        text = ''.join(segment.text for segment in strip)
        if needle not in text:
            continue
        found: list[tuple[str, float]] = []
        for segment in strip:
            if not segment.text.strip() or set(segment.text) <= CHROME:
                continue
            foreground, background = segment.style.color, segment.style.bgcolor
            if foreground is None or background is None:
                continue
            found.append((segment.text.strip(), _contrast(foreground, background)))
        return found
    raise AssertionError(f'{needle!r} was not painted at all')


def _app(tmp: str) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=store.create_session(),
    )


def _tree_app(tmp: str) -> tuple[SigilApp, AgentTreeStore]:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    session = store.create_session()
    trees = AgentTreeStore()
    trees.bind(session, root_name='wizard')
    app = SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=session,
        agent_tree=trees,
    )
    return app, trees


def _entry(short_id: str, *, title: str, current: bool = False) -> CatalogEntry:
    session = Session(id=short_id * 32, cwd='/repo', title=title)
    return CatalogEntry(session=session, workspace=Workspace(root=Path('/repo')), is_current=current)


def test_the_branch_of_the_selected_session_is_readable():
    """`/load`: the highlighted row keeps its workspace badge legible."""
    entries = [
        _entry('a', title='First', current=True),
        _entry('b', title='Second'),
    ]

    async def inner() -> list[tuple[str, float]]:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)) as pilot:
                app.push_screen(SessionPicker(entries))
                for _ in range(3):
                    await pilot.pause()
                return _spans(app, 'a' * 8)

    spans = asyncio.run(inner())

    assert spans, 'the selected row painted nothing to read'
    worst = min(ratio for _, ratio in spans)
    assert worst >= MIN_CONTRAST, f'the selected row has unreadable text: {spans}'


def test_a_running_row_of_the_tree_is_readable_while_selected():
    """The tree marks work in the accent colour; the cursor cannot eat it."""

    async def inner() -> list[tuple[str, float]]:
        with tempfile.TemporaryDirectory() as tmp:
            app, trees = _tree_app(tmp)
            tree = trees.active
            root = tree.ensure_root('wizard')
            node = tree.add(parent_id=root.id, name='pas_qa', role=AgentRole.SUBAGENT, task='qa the slice')
            async with app.run_test(size=(100, 30)) as pilot:
                app.push_screen(AgentTreeScreen(tree.nodes()))
                for _ in range(3):
                    await pilot.pause()
                return _spans(app, node.short_id)

    spans = asyncio.run(inner())

    assert any('running' in text for text, _ in spans), 'the row has to say what it is doing'
    worst = min(ratio for _, ratio in spans)
    assert worst >= MIN_CONTRAST, f'the selected row has unreadable text: {spans}'


def test_the_tree_still_opens_with_the_theme_in_place():
    """Sanity: the app of these tests paints with the shipped theme, not a default."""
    tree = AgentTreeStore().active
    tree.ensure_root('wizard')

    async def inner() -> str:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                return app.theme

    assert asyncio.run(inner()) == 'sigil'
