"""What a session knows about its context after it is loaded again.

Two numbers are measured per request and live in the process: the tokens the tool
schemas cost and the tokens the instruction blocks cost. They are what turns "the
history is this big" into "the next request fits", and they were lost on every
`/load` — the meter started at zero and `/context` reported a budget that was not
the one the conversation had.

The percentage and the tokens used never had this problem: they are derived from the
messages, which are rows. The fix keeps the same shape: what is measured is written
on the session, and the session is read back.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.test import TestModel

from src.engine.context.meter.context_meter import ContextMeter
from src.engine.sessions.session_store import SessionStore
from src.engine.storage.database import Database, database_path
from src.engine.storage.schema import USER_VERSION
from src.ui.app import SigilApp


def _messages() -> list:
    return [
        ModelRequest(parts=[UserPromptPart(content='hello')]),
        ModelResponse(parts=[TextPart(content='hey there')]),
    ]


# --- the row ------------------------------------------------------------------


def test_the_measured_overhead_survives_a_load(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()
    session.messages = _messages()
    session.tools_tokens = 21_200
    session.instructions_tokens = 9_300
    store.save(session)

    loaded = store.load(session.id)

    assert loaded is not None
    assert loaded.tools_tokens == 21_200
    assert loaded.instructions_tokens == 9_300


def test_a_session_that_measured_nothing_reports_zero_not_a_guess(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()
    session.messages = _messages()
    store.save(session)

    loaded = store.load(session.id)

    assert loaded is not None
    assert loaded.tools_tokens == 0
    assert loaded.instructions_tokens == 0


def test_an_older_database_gets_the_new_columns(tmp_path):
    """The database in `$SIGIL_HOME` was written before these columns existed."""
    base = tmp_path / 'store'
    path = database_path(base)
    Database.forget(path)
    store = SessionStore(cwd=tmp_path, base_dir=base)
    session = store.create_session()
    session.messages = _messages()
    store.save(session)

    # Back to the shape of the version before the measurement was persisted.
    store.database.execute('ALTER TABLE sessions DROP COLUMN tools_tokens')
    store.database.execute('ALTER TABLE sessions DROP COLUMN instructions_tokens')
    store.database.execute('ALTER TABLE sessions DROP COLUMN allow_external_files')
    store.database.execute('ALTER TABLE sessions DROP COLUMN mcp_overrides')
    store.database.execute('PRAGMA user_version = 1')
    Database.forget(path)

    reopened = SessionStore(cwd=tmp_path, base_dir=base)
    loaded = reopened.load(session.id)

    assert loaded is not None
    assert loaded.tools_tokens == 0
    assert loaded.instructions_tokens == 0
    # And what was already there is intact.
    assert len(loaded.messages) == 2
    assert reopened.database.value('PRAGMA user_version') == USER_VERSION


def test_the_meter_stamps_what_it_measured_onto_the_session():
    meter = ContextMeter(window=200_000)
    meter.set_overhead(instructions=9_000, tools=21_000)

    class Draft:
        tools_tokens = 0
        instructions_tokens = 0

    draft = Draft()
    meter.stamp(draft)

    assert draft.tools_tokens == 21_000
    assert draft.instructions_tokens == 9_000


# --- the app ------------------------------------------------------------------


def test_binding_a_session_restores_its_budget_and_drops_the_last_view():
    """The panel and `/context` read the meter, so the meter has to be told."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            session.messages = _messages()
            session.tools_tokens = 21_200
            session.instructions_tokens = 9_300
            store.save(session)
            agent = Agent(TestModel(custom_output_text='ok'), instructions='x')
            app = SigilApp(agent=agent, store=store, session=store.create_session(), meter=ContextMeter(window=200_000))
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                # A view measured on the previous conversation must not answer for
                # this one: the screen would show what the other session sent.
                assert app._context_capability is None or app._context_capability.last_view is None
                # The window belongs to the model, not to the session: binding a
                # conversation does not get to change it.
                window_before = app._meter.window

                app.bind_session(session)

                budget = app._meter.budget()
                assert budget.tools == 21_200
                assert budget.instructions == 9_300
                assert budget.window == window_before

    asyncio.run(inner())
