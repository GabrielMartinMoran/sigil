"""A cancelled turn leaves a history the next turn can use.

Cancelling inside a tool interrupts the run in the middle of a tool batch: the
calls that already answered keep their result, the rest never will. pydantic-ai
refuses a new prompt on top of a call that has no result, so the history that
gets saved closes the gaps. Without that, one `Esc` would break the session for
every message after it.
"""

import asyncio
import tempfile
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.core.interaction.question_hub import QuestionHub
from src.core.interaction.tools import register_interaction_tools
from src.core.sessions.session_store import SessionStore
from src.core.workspace.workspace_manager import WorkspaceManager
from src.ui.sigil_app import SigilApp
from src.ui.widgets.panels.question_panel import QuestionPanel


def _make_app(tmp: str, agent: Agent, hub: QuestionHub, *, workspace: Path | None = None) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    manager = WorkspaceManager(workspace) if workspace is not None else None
    return SigilApp(
        agent=agent,
        store=store,
        session=store.create_session(),
        question_hub=hub,
        workspace_manager=manager,
    )


async def _wait_for_panel(app, pilot, timeout: int = 60) -> None:
    for _ in range(timeout):
        await pilot.pause()
        if app.query_one(QuestionPanel).display:
            return


async def _wait_idle(app, pilot, timeout: int = 60) -> None:
    for _ in range(timeout):
        await pilot.pause()
        if not app._turn_active:
            return


def _returned_tools(app: SigilApp) -> set[str]:
    return {
        part.tool_name
        for message in app._session.messages
        for part in message.parts
        if type(part).__name__ == 'ToolReturnPart'
    }


def test_a_dangling_tool_call_is_closed_out_so_the_session_survives() -> None:

    async def inner() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            agent = Agent(TestModel(call_tools=['ask_user', 'other']), instructions='x')
            register_interaction_tools(agent, hub)

            @agent.tool_plain
            async def other() -> str:
                # Still running when the question gets cancelled: its call is
                # the one left without a result.
                await asyncio.sleep(0.5)
                return 'ok'

            app = _make_app(tmp, agent, hub)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.run_turn('decide for me')
                await _wait_for_panel(app, pilot)

                await pilot.press('escape')
                await _wait_idle(app, pilot)

                # Every call of the batch has a result, including the one that
                # was cut off mid-flight.
                assert _returned_tools(app) == {'ask_user', 'other'}

                # And the next message starts a new turn instead of failing on an
                # unprocessed call.
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot)
                texts = [str(widget.render()) for widget in app.query_one('#chat').children]
                assert not any('Error' in text for text in texts)
                assert any('hi' in text for text in texts)

    asyncio.run(inner())


def test_a_cancelled_turn_binds_the_selected_workspace_and_restores_the_process_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The standalone turn is an execution: its tools resolve the selected tree.

    The selected workspace is deliberately not the process directory, so a tool
    reading `workspace_root()` can only report it through the real binding: there
    is no other route to that value. The observer below delegates to the
    production helper and records only after the block has exited, on the same
    worker task — deleting the binding from `run_turn` would leave `after` empty
    and make the tool report `ambient`, so both assertions fail.
    """
    from contextlib import contextmanager

    from src.core.files.paths import workspace_root
    from tests.support.ui import wait_until

    ambient = tmp_path / 'ambient'
    selected = tmp_path / 'selected'
    ambient.mkdir()
    selected.mkdir()
    monkeypatch.chdir(ambient)

    hub = QuestionHub()
    recorded: list[Path] = []
    release = asyncio.Event()  # never set: the tool keeps the run in flight
    agent = Agent(TestModel(call_tools=['record_root', 'hold']), instructions='x')
    register_interaction_tools(agent, hub)

    @agent.tool_plain
    def record_root() -> str:
        # Sync tools run on a worker thread: this also proves the binding reaches
        # it, which the process directory alone could not.
        recorded.append(workspace_root())
        return 'root recorded'

    @agent.tool_plain
    async def hold() -> str:
        await release.wait()
        return 'released'

    app = _make_app(str(ambient), agent, hub, workspace=selected)
    binding_was_open = app._execution_workspace
    after: list[Path] = []

    @contextmanager
    def observing():
        # `finally`: a cancelled run raises through the block, and the point is to
        # see the task-local root after the production helper has reset it.
        try:
            with binding_was_open():
                yield
        finally:
            after.append(workspace_root())

    app._execution_workspace = observing

    async def inner() -> None:
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            app.run_turn('work in the selected tree')
            await wait_until(pilot, lambda: bool(recorded))

            assert Path.cwd() == ambient
            assert recorded == [selected.resolve()]

            # A real Escape twice: the first asks, the second confirms, and the
            # pending tool makes the run cancel rather than finish on its own.
            await pilot.press('escape')
            await pilot.pause()
            await pilot.press('escape')
            await wait_until(pilot, lambda: not app._turn_active, timeout=10)

            # Cleanup happened on the worker that ran the turn, not in this task.
            assert after == [ambient.resolve()]

    asyncio.run(inner())
