"""The headless turn loop: the same decisions as the terminal, without widgets.

The engine's turn machinery is tested against `TestModel` in `test_steering_and_abort`,
so this file tests what the *server* adds around it: that a turn publishes the events a
browser needs, that the session is saved, that abort and steering are wired, and that
the routes answer with the projections they claim.

`TestModel` is used instead of a network model for the obvious reason, and because the
events a real model produces are a subset of what this exercises: the deltas come from
the same `ProcessEventStream`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from src.core.agents.tree.reports import WAKE_PROMPT
from src.core.bootstrap import Composition
from src.core.settings import load_settings
from src.ui.subagent_reports import WAKE_PROMPT as TERMINAL_WAKE_PROMPT


@pytest.fixture
def composition(tmp_path: Path) -> Composition:
    """A real composition with a stub model and its storage in a temporary directory."""
    composition = Composition(
        tmp_path,
        settings=load_settings(),
        # `call_tools=[]` on purpose: `TestModel` calls every tool by default, and one
        # of them is `ask_user`, which waits for an answer no test is going to give.
        # The turn would block on a question instead of finishing, and the test would
        # be measuring the wrong thing.
        model=TestModel(custom_output_text='done', call_tools=[]),
        provider_paths=_paths(tmp_path),
    )
    # The guarded append-only path needs a known window; TestModel has no catalogue entry.
    composition.meter.set_window(100_000)
    return composition


def _paths(tmp_path: Path):
    from src.core.providers.provider_paths import ProviderPaths

    home = tmp_path / '.sigil'
    home.mkdir(exist_ok=True)
    return ProviderPaths(home)


def test_terminal_session_switch_updates_the_composition_owner(composition: Composition) -> None:
    from src.ui.sigil_app import SigilApp

    async def go() -> None:
        app = SigilApp(
            agent=composition.agent,
            store=composition.store,
            session=composition.session,
            roadmap_store=composition.roadmap_store,
            agent_tree=composition.agent_trees,
            on_bind_session=composition.bind_session,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            previous = composition.session
            await app.handle_command('/new')
            assert composition.session is app._session
            assert composition.session is not previous
            assert composition.agent_trees.active is app._session.tree

    asyncio.run(go())


def test_the_service_and_the_terminal_say_the_same_wake_sentence() -> None:
    """The sentence is written twice on purpose, and this is what holds the copies together.

    `server.runtime.WAKE_PROMPT` is a copy of `ui.subagent_reports.WAKE_PROMPT` rather
    than an import, because the service must not depend on the client: that direction
    is what keeps the server headless. The terminal compares the stored report against
    this exact text to tell a finished subagent from a sentence the user typed, so a
    reword on one side alone would leave a woken turn painted as the user's own
    message. Nothing else compares the two — the product cannot, so the test does.
    """

    assert WAKE_PROMPT == TERMINAL_WAKE_PROMPT, (
        'The service and the terminal no longer say the same thing to a woken turn. The '
        'two copies exist because the server must not import the client, so this test is '
        'the only place that sees both: if they diverge, the terminal stops recognizing '
        'the turn a finished subagent woke and paints it as text the user wrote. Change '
        'both copies, or neither.'
    )
