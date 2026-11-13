"""The seven UI changes, exercised through the app.

Each test drives the real widget tree, because most of these changes are about
what happens between widgets: a key that has to reach the composer, a theme that
has to reach the text, a preference that has to survive a restart.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from textual.containers import VerticalScroll

from src.engine.preferences import Preferences, PreferencesStore
from src.engine.roadmap.models.todo_item import TodoItem
from src.engine.roadmap.stores import RoadmapStores
from src.engine.sessions.session_store import SessionStore
from src.engine.themes import ThemeRegistry
from src.engine.tooling.presentation import display_name
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.sidebar_panel import SidebarPanel
from src.ui.widgets.status_bar import StatusBar
from src.ui.widgets.theme_picker_screen import ThemePickerScreen
from tests.ui_helpers import render_text, wait_for_turn

pytestmark = pytest.mark.usefixtures('memory_databases')


def _app(tmp: str, **kwargs) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    session = store.create_session()
    options = {
        'agent': Agent(TestModel(custom_output_text='ok'), instructions='x'),
        'store': store,
        'session': session,
        'preferences_store': PreferencesStore(Path(tmp) / 'ui.json'),
        'themes': ThemeRegistry.load(Path(tmp) / 'themes'),
    }
    options.update(kwargs)
    return SigilApp(**options)


# --- 1. Shift+Enter ---------------------------------------------------------


def test_shift_enter_inserts_a_line_and_enter_sends():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)) as pilot:
                field = app.query_one(PromptInput)
                field.focus()

                await pilot.press('a')
                await pilot.press('shift+enter')
                await pilot.press('b')
                assert field.value == 'a\nb'
                # Nothing was sent: the turn is the only thing that clears it.
                assert app._session.messages == []

                await pilot.press('enter')
                await wait_for_turn(app)
                assert not app._turn_active
                assert field.value == ''
                assert len(app._session.messages) == 2

    asyncio.run(inner())


def test_a_multi_line_message_reaches_the_turn_whole():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)) as pilot:
                field = app.query_one(PromptInput)
                field.focus()
                field.value = 'first line\nsecond line'
                await pilot.press('enter')
                await wait_for_turn(app)
                assert not app._turn_active

                from pydantic_ai.messages import UserPromptPart

                prompt = app._session.messages[0].parts[0]
                assert isinstance(prompt, UserPromptPart)
                assert prompt.content == 'first line\nsecond line'

    asyncio.run(inner())


def test_the_composer_grows_with_the_message_and_stops():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)) as pilot:
                field = app.query_one(PromptInput)
                one_line = field.size.height
                field.value = '\n'.join(str(i) for i in range(5))
                await pilot.pause()
                assert field.size.height > one_line

    asyncio.run(inner())


def test_whitespace_only_is_not_sent():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)) as pilot:
                field = app.query_one(PromptInput)
                field.focus()
                field.value = '   \n  '
                await pilot.press('enter')
                assert app._session.messages == []

    asyncio.run(inner())


# --- 2. Activity indicator --------------------------------------------------


def test_the_bar_is_idle_before_the_turn_and_spins_during_it():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                bar = app.query_one(StatusBar)
                assert 'Ready' in bar.render().plain

                app.set_activity(running=True, label='Thinking')
                assert 'Thinking' in bar.render().plain
                assert 'Ready' not in bar.render().plain

                app.set_activity(running=False)
                assert 'Ready' in bar.render().plain

    asyncio.run(inner())


def test_the_spinner_animates_and_stops():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                bar = app.query_one(StatusBar)
                app.set_activity(running=True, label='Thinking')
                first = bar.render().plain
                # The frame advances on a timer, not on the render call.
                bar._advance()
                second = bar.render().plain
                assert first != second

                app.set_activity(running=False)
                assert bar._timer is None

    asyncio.run(inner())


def test_the_label_follows_the_stream():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                bar = app.query_one(StatusBar)
                app.set_activity(running=True, label='Thinking')
                app.set_activity_label('Writing')
                assert 'Writing' in bar.render().plain

    asyncio.run(inner())


def test_the_elapsed_time_shows_only_after_a_second():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                bar = app.query_one(StatusBar)
                app.set_activity(running=True, label='Thinking')
                assert '0s' not in bar.render().plain

                app._turn_started_at -= 7
                app.activity_tick()
                assert '7s' in bar.render().plain

    asyncio.run(inner())


# --- 3. Branch, worktree and diff -------------------------------------------


def test_the_bar_shows_the_branch_and_the_diff(tmp_path):
    from src.engine.workspace.manager import WorkspaceManager

    root = tmp_path / 'repo'
    root.mkdir()
    _git(root, 'init', '-q')
    (root / 'a.txt').write_text('one\n', encoding='utf-8')
    _git(root, 'add', '.')
    _git(root, 'commit', '-q', '-m', 'init')
    (root / 'a.txt').write_text('one\ntwo\n', encoding='utf-8')

    async def inner():
        app = _app(str(tmp_path), workspace_manager=WorkspaceManager(root))
        async with app.run_test(size=(120, 30)):
            app.refresh_workspace_badge()
            app.refresh_diff()
            app._stop_diff_polling()
            await asyncio.wait_for(app.workers.wait_for_complete(), timeout=5)

            text = app.query_one(StatusBar).render().plain
            assert root.name in text or 'main' in text
            assert '+1' in text
            assert '−0' in text

    asyncio.run(inner())


def test_should_refresh_the_branch_after_an_external_checkout(tmp_path):
    from src.engine.workspace.manager import WorkspaceManager

    root = tmp_path / 'repo'
    root.mkdir()
    _git(root, 'init', '-q', '-b', 'original')
    (root / 'a.txt').write_text('one\n', encoding='utf-8')
    _git(root, 'add', '.')
    _git(root, 'commit', '-q', '-m', 'init')

    async def inner():
        app = _app(str(tmp_path), workspace_manager=WorkspaceManager(root))
        async with app.run_test(size=(120, 30)):
            app._stop_diff_polling()
            await app.workers.wait_for_complete()
            bar = app.query_one(StatusBar)
            assert 'original' in bar.plain()

            _git(root, 'checkout', '-q', '-b', 'updated')
            await app.refresh_diff().wait()
            assert 'updated' in bar.plain()
            assert 'original' not in bar.plain()

            _git(root, 'checkout', '-q', '--detach')
            await app.refresh_diff().wait()
            assert 'updated' not in bar.plain()
            assert 'main@' in bar.plain()

    asyncio.run(inner())


def test_without_git_the_bar_says_nothing_about_changes():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                text = app.query_one(StatusBar).render().plain
                assert '+' not in text
                assert '−' not in text

    asyncio.run(inner())


def test_the_diff_summary_paints_additions_green_and_deletions_red():
    from src.engine.workspace.diff import DiffStat
    from src.ui.widgets.status_bar import StatusBar as Bar

    bar = Bar()
    bar.set_diff(DiffStat(added=12, removed=3))
    styles = {span.style for span in bar._right().spans}
    # Whatever the theme says, the two numbers must not share a color.
    assert len({style for style in styles if style}) >= 2


# --- 4. One sidebar ---------------------------------------------------------


def test_the_sidebar_is_one_panel_with_the_roadmap_as_its_last_section():
    """One box, one scroll, and the plan at the end of the same list.

    The plan used to live in a box of its own under the diagnostics. Both are read
    together, so the split only cost a border, a margin and the height of the
    column, which sat mostly empty.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            roadmap = RoadmapStores(session_id=None, base_dir=Path(tmp))
            roadmap.root.set_items([TodoItem(title='Ship it', status='in_progress')])
            app = _app(tmp, roadmap_store=roadmap)
            async with app.run_test(size=(120, 40)):
                sidebar = app.query_one('#sidebar')
                assert type(sidebar).__name__ == 'SidebarPanel'
                # One box: the scroll and the body inside it, and nothing else. The
                # scroll is a `ReadOnlyScroll` — the panel is read, never typed
                # into — which is a `VerticalScroll` all the same.
                children = list(sidebar.children)
                assert len(children) == 1
                assert isinstance(children[0], VerticalScroll)

                panel = app.query_one(SidebarPanel)
                text = panel.text
                assert 'Model' in text
                assert 'Loaded skills' in text
                # The plan is the last section of the same text, not a second widget.
                assert 'Roadmap' in text
                assert text.index('Loaded skills') < text.index('Roadmap')
                assert text.index('Roadmap') < text.index('Ship it')

    asyncio.run(inner())


def test_the_panel_scrolls_when_a_long_plan_does_not_fit():
    """Twenty items in a short terminal: the panel keeps its height and scrolls.

    One scrollbar for the whole column, not a scrolling roadmap inside a scrolling
    panel: there are 34 columns of room and two of them would already be borders.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            roadmap = RoadmapStores(session_id=None, base_dir=Path(tmp))
            roadmap.root.set_items([TodoItem(title=f'Task {index}') for index in range(20)])
            app = _app(tmp, roadmap_store=roadmap)
            async with app.run_test(size=(100, 24)) as pilot:
                panel = app.query_one(SidebarPanel)
                scroller = app.query_one('#sidebar-scroll')
                prompt = app.query_one(PromptInput)

                # The content is taller than the box, and the box is still on screen.
                assert scroller.virtual_size.height > scroller.size.height
                assert scroller.max_scroll_y > 0
                assert panel.region.bottom <= prompt.region.y

                # Scrolling reaches the end of the plan, which is the point.
                scroller.scroll_to(y=scroller.max_scroll_y, animate=False)
                await pilot.pause()
                assert scroller.scroll_y > 0
                assert 'Task 19' in panel.text

    asyncio.run(inner())


def test_the_panel_scrolls_when_there_are_more_skills_than_rows():
    """Twenty loaded skills must not hide the plan: the panel scrolls, it does not grow."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            roadmap = RoadmapStores(session_id=None, base_dir=Path(tmp))
            roadmap.root.set_items([TodoItem(title='The only task')])
            app = _app(tmp, roadmap_store=roadmap)
            async with app.run_test(size=(100, 26)) as pilot:
                panel = app.query_one(SidebarPanel)
                panel.update_skills([f'skill-{index}' for index in range(20)])
                await pilot.pause()

                scroller = app.query_one('#sidebar-scroll')
                assert scroller.max_scroll_y > 0
                # The plan is below the fold, and it is still there when scrolled to.
                assert 'The only task' in panel.text
                scroller.scroll_to(y=scroller.max_scroll_y, animate=False)
                await pilot.pause()
                assert scroller.scroll_y > 0

    asyncio.run(inner())


def test_the_roadmap_still_updates_live():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            roadmap = RoadmapStores(session_id=None, base_dir=Path(tmp))
            app = _app(tmp, roadmap_store=roadmap)
            async with app.run_test(size=(120, 40)) as pilot:
                panel = app.query_one(SidebarPanel)
                assert 'No tasks yet' in panel.text
                assert 'Roadmap' in panel.text

                roadmap.root.set_items([TodoItem(title='First task', status='pending')])
                await pilot.pause()
                assert 'First task' in panel.text

    asyncio.run(inner())


def test_the_panel_without_a_roadmap_store_has_no_roadmap_section():
    """No store, no plan: the section stays away instead of announcing a missing feature."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)):
                text = app.query_one(SidebarPanel).text
                assert 'Model' in text
                assert 'Roadmap' not in text
                assert 'No tasks yet' not in text

    asyncio.run(inner())


def test_hiding_the_sidebar_hides_the_roadmap_with_it():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)):
                panel = app.query_one(SidebarPanel)
                await app.handle_command('/sidebar', 'off')
                assert panel.display is False

                await app.handle_command('/sidebar', 'on')
                assert panel.display is True

    asyncio.run(inner())


# --- 5. Reasoning visibility ------------------------------------------------


def test_reasoning_is_collapsed_by_default():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                chat = app.query_one(ChatLog)
                assert chat.reasoning_visible is False

                await chat.begin_response()
                await chat.add_thinking('secret reasoning')
                chat.finalize()

                from src.ui.widgets.chat_log import AssistantBlock

                block = next(child for child in chat.children if isinstance(child, AssistantBlock))
                text = render_text(block._build())
                assert 'Reasoning' in text
                assert 'secret reasoning' not in text

    asyncio.run(inner())


def test_the_command_expands_and_collapses_in_place():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                chat = app.query_one(ChatLog)
                await chat.begin_response()
                await chat.add_thinking('secret reasoning')
                chat.finalize()

                from src.ui.widgets.chat_log import AssistantBlock

                block = next(child for child in chat.children if isinstance(child, AssistantBlock))

                await app.handle_command('/reasoning-visibility', 'on')
                assert chat.reasoning_visible is True
                assert 'secret reasoning' in render_text(block._build())

                await app.handle_command('/reasoning-visibility', 'off')
                assert 'secret reasoning' not in render_text(block._build())

    asyncio.run(inner())


def test_the_command_toggles_without_an_argument():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                await app.handle_command('/reasoning-visibility')
                assert app._preferences.reasoning_visible is True

                await app.handle_command('/reasoning-visibility')
                assert app._preferences.reasoning_visible is False

    asyncio.run(inner())


def test_the_preference_survives_a_restart():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = PreferencesStore(Path(tmp) / 'ui.json')
            first = _app(tmp)
            async with first.run_test(size=(100, 30)):
                await first.handle_command('/reasoning-visibility', 'on')

            assert store.load().reasoning_visible is True

            second = _app(tmp)
            async with second.run_test(size=(100, 30)):
                assert second.query_one(ChatLog).reasoning_visible is True

    asyncio.run(inner())


def test_an_unknown_argument_reports_the_usage():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                await app.handle_command('/reasoning-visibility', 'maybe')
                texts = [render_text(child.render()) for child in app.query_one(ChatLog).children]
                assert any('Usage: /reasoning-visibility' in text for text in texts)

    asyncio.run(inner())


# --- 6. Tool call blocks ----------------------------------------------------


def test_a_tool_call_and_its_result_share_one_block():
    """The result lands inside the call, and `expanded` is what makes it visible."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                chat = app.query_one(ChatLog)
                await chat.add_tool_call('read_file', {'path': 'a.py'})
                await chat.add_tool_result('read_file', 'line one\nline two')

                from src.ui.widgets.chat_log import ToolCallBlock

                blocks = [child for child in chat.children if isinstance(child, ToolCallBlock)]
                assert len(blocks) == 1
                blocks[0].set_expanded(True)
                text = render_text(blocks[0]._build())
                # The row shows the tool's display name, not its callable name: `read`,
                # not `read_file`. See `engine.tooling.presentation`.
                assert display_name('read_file') in text
                assert 'a.py' in text
                assert 'line one' in text
                assert '✓' in text

    asyncio.run(inner())


def test_the_block_indents_the_output_under_a_gutter():
    from src.ui.widgets.chat_log import ToolCallBlock

    block = ToolCallBlock('run', {'command': 'ls'})
    block.set_result('a.txt\nb.txt')
    lines = render_text(block._build()).splitlines()
    assert all(line.startswith('  │ ') for line in lines if line.strip())


def test_long_output_is_truncated_with_a_count():
    from src.ui.widgets.chat_log import ToolCallBlock

    block = ToolCallBlock('run', {'command': 'seq 100'})
    block.set_result('\n'.join(str(i) for i in range(40)))
    assert '… 40 lines' in render_text(block._build())
    block.set_expanded(True)
    text = render_text(block._build())
    assert '… +34 more' in text
    assert '39' not in text


def test_a_pending_call_is_marked_as_running():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)) as pilot:
                chat = app.query_one(ChatLog)
                await chat.add_tool_call('run', {'command': 'sleep 10'})
                await pilot.pause()

                from src.ui.widgets.chat_log import ToolCallBlock

                block = next(child for child in chat.children if isinstance(child, ToolCallBlock))
                assert 'running' in render_text(block._build())
                assert block._state_mark() != '✓'

    asyncio.run(inner())


def test_indentation_of_the_output_is_kept():
    """A diff or a snippet is unreadable without its leading whitespace."""
    from src.ui.widgets.chat_log import ToolCallBlock

    block = ToolCallBlock('read_file', {'path': 'a.py'})
    block.set_result('def f():\n    return 1')
    block.set_expanded(True)
    assert '│     return 1' in render_text(block._build())


def test_several_arguments_are_readable():
    from src.ui.widgets.chat_log import format_tool_args

    assert format_tool_args({'path': 'a.py'}) == 'a.py'
    assert format_tool_args({'path': 'a.py', 'limit': 10}) == 'path=a.py, limit=10'
    assert format_tool_args({}) == ''


# --- 7. Themes --------------------------------------------------------------


def test_the_builtin_themes_are_registered_and_the_default_applies():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                assert app.theme == 'sigil'
                assert 'synthwave' in app.available_themes

    asyncio.run(inner())


@pytest.mark.parametrize('theme', ['dark', 'synthwave'])
def test_a_stored_theme_is_applied_on_start(theme):
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            PreferencesStore(Path(tmp) / 'ui.json').save(Preferences(theme=theme))
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                assert app.theme == theme

    asyncio.run(inner())


def test_an_installed_theme_shows_up(tmp_path):
    themes = tmp_path / 'themes'
    themes.mkdir()
    (themes / 'mine.json').write_text(
        json.dumps({'name': 'mine', 'colors': {'primary': '#ff0000', 'accent': '#00ff00'}}),
        encoding='utf-8',
    )

    async def inner():
        app = _app(str(tmp_path), themes=ThemeRegistry.load(themes))
        async with app.run_test(size=(100, 30)):
            assert 'mine' in app.available_themes

    asyncio.run(inner())


def test_the_command_switches_the_theme_and_remembers_it():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                await app.handle_command('/theme', 'synthwave')
                assert app.theme == 'synthwave'
                assert PreferencesStore(Path(tmp) / 'ui.json').load().theme == 'synthwave'

    asyncio.run(inner())


def test_an_unknown_theme_lists_what_is_installed():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                await app.handle_command('/theme', 'nope')
                texts = [render_text(child.render()) for child in app.query_one(ChatLog).children]
                joined = ' '.join(texts)
                assert 'No theme called' in joined
                assert 'synthwave' in joined

    asyncio.run(inner())


def test_the_picker_previews_on_highlight_and_restores_on_cancel():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)) as pilot:
                app.run_worker(app.handle_command('/theme'))
                for _ in range(10):
                    await pilot.pause()
                    if isinstance(app.screen, ThemePickerScreen):
                        break
                assert isinstance(app.screen, ThemePickerScreen)

                await pilot.press('down', 'down')
                # Moving the highlight is what changes the app behind the modal.
                assert app.theme == 'synthwave'

                await pilot.press('escape')
                for _ in range(5):
                    await pilot.pause()
                    if not isinstance(app.screen, ThemePickerScreen) and app.theme == 'sigil':
                        break
                assert not isinstance(app.screen, ThemePickerScreen)
                assert app.theme == 'sigil'
                assert PreferencesStore(Path(tmp) / 'ui.json').load().theme == 'sigil'

    asyncio.run(inner())


def test_the_picker_applies_the_highlighted_theme_on_enter():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)) as pilot:
                app.run_worker(app.handle_command('/theme'))
                for _ in range(10):
                    await pilot.pause()
                    if isinstance(app.screen, ThemePickerScreen):
                        break
                assert isinstance(app.screen, ThemePickerScreen)

                await pilot.press('down', 'down')
                await pilot.press('enter')
                for _ in range(5):
                    await pilot.pause()
                    if not isinstance(app.screen, ThemePickerScreen):
                        break
                assert not isinstance(app.screen, ThemePickerScreen)
                assert app.theme == 'synthwave'
                assert PreferencesStore(Path(tmp) / 'ui.json').load().theme == 'synthwave'

    asyncio.run(inner())


def test_a_theme_change_repaints_the_text_and_not_only_the_boxes():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)) as pilot:
                panel = app.query_one(SidebarPanel)
                before = {span.style for span in panel._build().spans if span.style}
                app.theme = 'synthwave'
                await pilot.pause()
                after = {span.style for span in panel._build().spans if span.style}
                assert before != after

    asyncio.run(inner())


def test_theme_list_reports_the_installed_ones():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)):
                await app.handle_command('/theme', '--list')
                texts = [render_text(child.render()) for child in app.query_one(ChatLog).children]
                joined = ' '.join(texts)
                assert 'dark' in joined
                assert 'synthwave' in joined
                assert 'builtin' in joined

    asyncio.run(inner())


def _git(cwd: Path, *args: str) -> None:
    import subprocess

    subprocess.run(
        ['git', *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            'GIT_AUTHOR_NAME': 'Test',
            'GIT_AUTHOR_EMAIL': 'test@example.com',
            'GIT_COMMITTER_NAME': 'Test',
            'GIT_COMMITTER_EMAIL': 'test@example.com',
            'PATH': '/usr/bin:/bin:/usr/local/bin',
            'HOME': str(cwd),
        },
    )


def test_markdown_uses_theme_colors_and_repaints_on_theme_change():
    from rich.color import Color

    from src.ui.widgets.chat_log import AssistantBlock

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 30)) as pilot:
                chat = app.query_one(ChatLog)
                await chat.add_text(
                    '## Heading\n\n`inline` [link](https://example.com)\n\n```python\ndef example():\n    return 42\n```'
                )
                chat.finalize()
                block = chat.query_one(AssistantBlock)

                def rendered():
                    return list(app.console.render(block._build(), app.console.options.update_width(80)))

                def foreground(segments, text):
                    return next(s.style.color for s in segments if text in s.text and s.style and s.style.color)

                original_visual = block.render()
                first = rendered()
                assert foreground(first, 'Heading') == Color.parse(app.theme_variables['primary'])
                assert foreground(first, 'inline') == Color.parse(app.theme_variables['primary'])
                assert any(s.style and s.style.link == 'https://example.com' for s in first)
                assert foreground(first, 'def') == Color.parse(app.theme_variables['primary'])

                app.theme = 'synthwave'
                await pilot.pause()
                assert block.render() is not original_visual
                second = rendered()
                assert foreground(second, 'Heading') == Color.parse(app.theme_variables['primary'])
                assert foreground(second, 'inline') == Color.parse(app.theme_variables['primary'])
                assert foreground(second, 'def') == Color.parse(app.theme_variables['primary'])
                assert any(s.style and s.style.link == 'https://example.com' for s in second)

    asyncio.run(inner())
