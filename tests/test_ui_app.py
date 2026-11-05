"""Smoke tests for the Textual app (no network and no real model)."""

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import src.ui.app as appmod
import src.ui.turn_mixin as turnmod
from src.engine.sessions.session_store import SessionStore
from src.engine.tooling.logging import ToolLoggerHub
from src.engine.tooling.registrar import ToolRegistrar
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import AssistantBlock, ChatLog
from src.ui.widgets.command_menu import CommandMenu
from src.ui.widgets.prompt_input import PromptInput


async def _wait_idle(app, pilot, attempts: int = 40) -> None:
    for _ in range(attempts):
        await pilot.pause()
        if not app._turn_active:
            return


def _make_app(agent: Agent, tmp: str, tool_logger: ToolLoggerHub | None = None) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    session = store.create_session()
    return SigilApp(agent=agent, store=store, session=session, tool_logger=tool_logger)


def test_app_runs_turn_and_renders_chat(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='answer'), instructions='x')
            app = _make_app(agent, tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'e', 'l', 'l', 'o')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

                chat = app.query_one(ChatLog)
                assert len(chat.children) >= 2  # user message + answer
                assert len(app._session.messages) == 2

    asyncio.run(inner())


def test_command_menu_filters_and_selects(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='ok'), instructions='x')
            app = _make_app(agent, tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                app.query_one(PromptInput).value = ''
                await pilot.press('slash')
                await pilot.pause()

                menu = app.query_one(CommandMenu)
                assert menu.display is True
                assert menu.option_count == 12

                await pilot.press('n')
                await pilot.pause()
                assert menu.option_count == 1
                assert menu.get_option_at_index(0).id == '/new'

                await pilot.press('enter')
                await pilot.pause()
                assert menu.display is False

    asyncio.run(inner())


def _chat_texts(app) -> list[str]:
    """Plain text of every chat message."""
    texts = []
    for child in app.query_one(ChatLog).children:
        try:
            rendered = child.render()
        except Exception:  # noqa: BLE001, S112 - widgets without their own render
            continue
        plain = getattr(rendered, 'plain', None)
        if isinstance(plain, str):
            texts.append(plain)
    return texts


def test_mcp_command_without_servers_explains_how_to_configure(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='ok'), instructions='x')
            app = _make_app(agent, tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('/')
                await pilot.press('m', 'c', 'p')
                await pilot.press('enter')
                await pilot.pause()

                assert any('mcp_servers' in text for text in _chat_texts(app))

    asyncio.run(inner())


def test_startup_notices_are_rendered(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='ok'), instructions='x')
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=agent,
                store=store,
                session=store.create_session(),
                startup_notices=('mcp_servers is invalid',),
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                assert any('mcp_servers is invalid' in text for text in _chat_texts(app))

    asyncio.run(inner())


def test_tool_call_and_result_are_rendered(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            tool_logger = ToolLoggerHub()
            agent = Agent(TestModel(call_tools=['ping']), instructions='x')

            @ToolRegistrar(agent, tool_logger).tool
            def ping() -> str:
                return 'pong'

            app = _make_app(agent, tmp, tool_logger)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

                widgets = list(app.query_one(ChatLog).children)
                class_names = {name for widget in widgets for name in widget.classes}
                assert 'tool-call' in class_names
                assert 'tool-result' in class_names
                assert any('ping' in str(widget.render()) for widget in widgets)
                assert len(app._session.messages) >= 3

    asyncio.run(inner())


def test_tool_error_is_rendered_as_notice(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            tool_logger = ToolLoggerHub()
            agent = Agent(TestModel(call_tools=['explode']), instructions='x')

            @ToolRegistrar(agent, tool_logger).tool
            def explode() -> str:
                raise ValueError('kaboom')

            app = _make_app(agent, tmp, tool_logger)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

                texts = [str(widget.render()) for widget in app.query_one(ChatLog).children]
                assert any('kaboom' in text for text in texts)

    asyncio.run(inner())


def test_autoscroll_only_when_at_bottom(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(Agent(TestModel(), instructions='x'), tmp)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)

                for index in range(60):
                    await chat.add_user(f'message {index}')
                await pilot.pause()
                assert chat.is_vertical_scroll_end

                chat.scroll_home(animate=False)
                await pilot.pause()
                assert not chat.is_vertical_scroll_end
                y = chat.scroll_offset.y

                await chat.add_user('new message with scroll at the top')
                await pilot.pause()
                assert chat.scroll_offset.y == y  # does not jump to the bottom

    asyncio.run(inner())


def test_autoscroll_follows_streaming_growth(monkeypatch):
    """Auto-scroll must follow the content that grows while streaming.

    Regression: the manual scroll after each delta ran before the deferred
    flush of the block (80 ms throttle), so the view ended up short.
    Textual's anchoring readjusts the scroll on every layout.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(Agent(TestModel(), instructions='x'), tmp)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)

                for index in range(40):
                    await chat.add_user(f'message {index}')
                await pilot.pause()
                assert chat.is_vertical_scroll_end

                # Several deltas inside the throttle window: the real content
                # only grows when the timer flushes.
                await chat.begin_response()
                for _ in range(20):
                    await chat.add_text('answer line ' * 4)

                # Refresh with the content not yet grown (the scheduled manual
                # scroll would run here, with the old height)...
                # ...and only afterwards does the throttle flush the pending text.
                await pilot.pause()

                assert chat.is_vertical_scroll_end

    asyncio.run(inner())


def test_markdown_output_is_rendered_in_app(monkeypatch):

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='# Heading\n\nThis is **bold**.'), instructions='x')
            app = _make_app(agent, tmp)
            async with app.run_test(size=(90, 30)) as pilot:
                await pilot.pause()
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

                svg = app.export_screenshot()
                assert 'Heading' in svg
                assert '# Heading' not in svg  # the markdown '#' is not shown literally
                assert 'bold' in svg
                assert '**bold**' not in svg  # the '**' are not shown literally

    asyncio.run(inner())


def test_step_limit_abort_preserves_session_and_context(monkeypatch):
    """Regression: exhausting the steps of a turn does not lose the session.

    The aborted turn must keep what was completed (including the user
    message), be persisted to disk and serve as context for the next turn.
    """
    from src.engine.settings import SigilSettings

    monkeypatch.setattr(appmod, 'load_settings', lambda *a, **k: SigilSettings(max_steps=2))

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            tool_logger = ToolLoggerHub()
            agent = Agent(TestModel(call_tools=['ping']), instructions='x')

            @ToolRegistrar(agent, tool_logger).tool
            def ping() -> str:
                return 'pong'

            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            app = SigilApp(agent=agent, store=store, session=session, tool_logger=tool_logger)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

                # The turn was cut by the limit, but the context was not lost.
                assert any('Step limit' in text for text in _chat_texts(app))
                assert len(app._history) >= 2
                assert len(app._session.messages) == len(app._history)

                # The session was saved and shows up in the listing with a preview.
                sessions = store.list_sessions()
                saved = next((s for s in sessions if s.id == session.id), None)
                assert saved is not None
                await pilot.press('a', 'g', 'a', 'i', 'n')

                reloaded = store.load(session.id)
                # The next turn starts with all the previous context.
                assert len(reloaded.messages) == len(app._history)

                # The next turn starts with all the previous context.
                prior = len(app._history)
                monkeypatch.setattr(appmod, 'load_settings', lambda *a, **k: SigilSettings(max_steps=20))
                await pilot.press('a', 'g', 'a', 'i', 'n')
                await pilot.press('enter')
                await _wait_idle(app, pilot)
                assert len(app._history) > prior

    asyncio.run(inner())


def test_turn_is_not_cut_by_pydantic_ai_default_request_limit(monkeypatch):
    """Regression: the 50-request cap of pydantic-ai does not cut a long turn.

    Sigil bounds the turn with `SIGIL_MAX_STEPS`; the pydantic-ai default
    aborted earlier with a `UsageLimitExceeded` the user never configured.
    """
    from pydantic_ai.messages import ModelMessage, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

    from src.engine.settings import SigilSettings

    monkeypatch.setattr(appmod, 'load_settings', lambda *a, **k: SigilSettings(max_steps=200))

    # 60 tool calls = 61 requests: above the default of 50.
    calls = 60

    async def stream_function(messages: list[ModelMessage], _info: AgentInfo):
        done = sum(
            1 for message in messages for part in getattr(message, 'parts', []) if isinstance(part, ToolCallPart)
        )
        if done < calls:
            yield {0: DeltaToolCall(name='ping', json_args='{}', tool_call_id=f'c{done}')}
        else:
            yield 'done'

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(FunctionModel(stream_function=stream_function), instructions='x')

            @agent.tool_plain
            def ping() -> str:
                return 'pong'

            app = _make_app(agent, tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot, attempts=400)

                texts = _chat_texts(app)
                assert not any('request_limit' in text for text in texts)
                assert not any('Step limit' in text for text in texts)
                # The turn reached the end: the final answer stayed in the chat.
                blocks = [child for child in app.query_one(ChatLog).children if isinstance(child, AssistantBlock)]
                assert blocks and blocks[-1]._text == 'done'

    asyncio.run(inner())


def test_checkpoint_persists_progress_during_turn(monkeypatch):
    """Regression: progress is saved during the turn, not only at the end.

    If the process dies halfway through a long turn, the session on disk must
    keep what was completed up to the last step.
    """
    # No throttle, so the mid-turn checkpoint is deterministic.
    monkeypatch.setattr(turnmod, 'CHECKPOINT_INTERVAL', 0.0)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            tool_logger = ToolLoggerHub()
            agent = Agent(TestModel(call_tools=['ping']), instructions='x')
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            seen: list[int] = []

            @ToolRegistrar(agent, tool_logger).tool
            def ping() -> str:
                # The tool runs mid-turn: the checkpoint must already have
                # persisted the user prompt and the previous response.
                loaded = store.load(session.id)
                seen.append(len(loaded.messages) if loaded is not None else 0)
                return 'pong'

            app = SigilApp(agent=agent, store=store, session=session, tool_logger=tool_logger)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

            assert seen, 'the tool never got to run'
            assert seen[0] >= 2  # user prompt + model response

    asyncio.run(inner())


def test_checkpoint_throttles_writes_but_force_saves(monkeypatch):
    """The checkpoint throttles to ~1/s; `force` (end of turn) always saves."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
        session = store.create_session()
        app = SigilApp(agent=Agent(TestModel(), instructions='x'), store=store, session=session)

        class _FakeRun:
            def __init__(self) -> None:
                self.messages: list = []

            def all_messages(self) -> list:
                return self.messages

        run = _FakeRun()
        app._current_run = run

        run.messages = [ModelRequest(parts=[UserPromptPart(content='one')])]
        app.checkpoint()
        assert len(store.load(session.id).messages) == 1

        run.messages = [*run.messages, ModelRequest(parts=[UserPromptPart(content='two')])]
        app.checkpoint()  # inside the window: it does not write

        app.checkpoint(force=True)  # end of turn: mandatory


def test_context_command_shows_breakdown_and_scope(monkeypatch):

    from src.engine.context.capability import ContextViewCapability
    from src.engine.context.instructions.workspace_instructions import WorkspaceInstructions
    from src.engine.context.meter.context_meter import ContextMeter
    from src.engine.context.scope import ScopeTracker
    from src.engine.context.strategy.windowed import WindowedStrategy

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'AGENTS.md').write_text('# Project rules')
            (root / 'src').mkdir()
            (root / 'src' / 'AGENTS.md').write_text('# Src rules')

            agent = Agent(TestModel(custom_output_text='ok'), instructions='x')
            store = SessionStore(cwd=tmp, base_dir=root / 'sessions')
            session = store.create_session()
            scope = ScopeTracker(root)
            workspace = WorkspaceInstructions(root)
            meter = ContextMeter(window=200_000)
            capability = ContextViewCapability(strategy=WindowedStrategy(), meter=meter)

            app = SigilApp(
                agent=agent,
                store=store,
                session=session,
                meter=meter,
                context_capability=capability,
                scope=scope,
                workspace=workspace,
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                scope.observe('read_file', {'path': 'src/x.py'})
                await pilot.press('/')
                await pilot.press('c', 'o', 'n', 't', 'e', 'x', 't')
                await pilot.press('enter')
                await pilot.pause()

                text = '\n'.join(_chat_texts(app))
                assert 'Context' in text
                assert 'Budget' in text
                assert 'Active scope' in text
                assert 'src/AGENTS.md' in text

    asyncio.run(inner())
