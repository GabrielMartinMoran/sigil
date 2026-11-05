"""UI tests for workspace switching: `/new --worktree` and cross-worktree `/load`."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models.test import TestModel
from textual.widgets import OptionList

from src.engine.roadmap.store import RoadmapStore
from src.engine.sessions.session_store import SessionStore
from src.engine.skills.loaded import LoadedSkills
from src.engine.workspace.manager import WorkspaceManager
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ['git', '-c', 'user.email=test@example.com', '-c', 'user.name=Test', *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / 'project'
    root.mkdir()
    _git(root, 'init', '-q')
    (root / 'README.md').write_text('hello\n')
    _git(root, 'add', '.')
    _git(root, 'commit', '-q', '-m', 'init')
    return root


def _app(
    repo: Path,
    tmp_path: Path,
    *,
    sessions: list[str] = (),
    scope: object | None = None,
) -> SigilApp:
    store = SessionStore(cwd=repo, base_dir=tmp_path / 'sessions')
    session = store.create_session()
    if sessions:
        session.messages = [ModelRequest(parts=[UserPromptPart(content=text)]) for text in sessions]
        store.save(session)

    roadmap = RoadmapStore(directory=tmp_path / 'roadmaps', session_id=session.id)
    manager = WorkspaceManager(
        repo,
        worktrees_dir=tmp_path / 'worktrees',
        session_store=store,
        roadmap_store=roadmap,
        rebindables=[scope] if scope is not None else None,
    )
    return SigilApp(
        agent=Agent(TestModel(), instructions='x'),
        store=store,
        session=session,
        roadmap_store=roadmap,
        loaded_skills=LoadedSkills(),
        workspace_manager=manager,
        scope=scope,
    )


def _texts(app: SigilApp) -> list[str]:
    return [child.render().plain for child in app.query_one(ChatLog).children]


def test_new_without_arguments_stays_in_the_same_workspace(monkeypatch, tmp_path, repo):
    monkeypatch.chdir(repo)

    async def inner():
        app = _app(repo, tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.handle_command('/new')
            await pilot.pause()

            assert Path.cwd() == repo
            assert app.current_workspace_label() == 'main'
            assert not (tmp_path / 'worktrees').exists()

    asyncio.run(inner())


def test_new_with_worktree_creates_it_and_works_there(monkeypatch, tmp_path, repo):
    monkeypatch.chdir(repo)

    async def inner():
        app = _app(repo, tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.handle_command('/new', '--worktree fix-login')
            await pilot.pause()

            expected = tmp_path / 'worktrees' / 'project' / 'fix-login'
            assert Path.cwd() == expected
            assert app.current_workspace_label() == 'fix-login'
            assert app._session.cwd == str(expected)
            # The session is persisted under the new workspace, not the old one.
            assert app._store.cwd == str(expected)

            notices = '\n'.join(_texts(app))
            assert 'main → fix-login' in notices
            assert 'branch: sigil/fix-login' in notices
            # The worktree starts from HEAD: the ignored files are not there.
            assert (expected / 'README.md').is_file()
            assert not (expected / '.env').exists()

    asyncio.run(inner())


def test_the_picker_shows_every_workspace_and_marks_the_current_one(monkeypatch, tmp_path, repo):
    """The listing is the feature: a session of another worktree has to be visible.

    This drives the real modal, exactly as the app does (`run_worker`), because the
    picker is the only way the user reaches a session of another workspace.
    """
    monkeypatch.chdir(repo)

    async def inner():
        app = _app(repo, tmp_path)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            app._session.messages = [ModelRequest(parts=[UserPromptPart(content='main task')])]
            app._store.save(app._session)
            await app.handle_command('/new', '--worktree picker')
            await pilot.pause()
            worktree_session = app._session.id

            app.switch_workspace(str(repo))
            await pilot.pause()

            app.run_worker(app.handle_command('/load'))
            for _ in range(20):
                await pilot.pause()
                if len(app.screen_stack) > 1:
                    break

            options = app.screen.query_one(OptionList)
            listed = [options.get_option_at_index(index).prompt.plain for index in range(options.option_count)]
            assert any('picker' in line for line in listed)
            assert any('main' in line for line in listed)
            assert any(line.startswith('●') for line in listed)

            target = next(
                index
                for index in range(options.option_count)
                if options.get_option_at_index(index).id == worktree_session
            )
            options.highlighted = target
            await pilot.press('enter')
            for _ in range(20):
                await pilot.pause()

            assert app._session.id == worktree_session
            assert app.current_workspace_label() == 'picker'

    asyncio.run(inner())


def test_tools_operate_on_the_new_worktree_after_the_switch(monkeypatch, tmp_path, repo):
    """The point of the switch: the agent works *on the worktree*, not on the old tree."""
    import asyncio as _asyncio

    monkeypatch.chdir(repo)

    async def inner():
        app = _app(repo, tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.handle_command('/new', '--worktree isolated')
            await pilot.pause()

            from src.engine.files.tools import register_file_tools
            from src.engine.shell.runner import execute_commands

            class Recorder:
                def __init__(self) -> None:
                    self.tools: dict[str, object] = {}

                def _register(self, func, kwargs):
                    self.tools[kwargs.get('name') or func.__name__] = func
                    return func

                def tool(self, func, **kwargs):
                    return self._register(func, kwargs)

                def tool_plain(self, func, **kwargs):
                    return self._register(func, kwargs)

            recorder = Recorder()
            register_file_tools(recorder)
            written = recorder.tools['write_file'](path='only-here.txt', content='x')

            assert Path(written.path) == tmp_path / 'worktrees' / 'project' / 'isolated' / 'only-here.txt'
            assert not (repo / 'only-here.txt').exists()

            pwd = await execute_commands(['pwd'])
            assert pwd[0].stdout.strip() == str(tmp_path / 'worktrees' / 'project' / 'isolated')

            # The parent tree is out of reach for the file tools. The sandbox of
            # the test lives under `/tmp`, which the tools allow, so the temp
            # allowance is emptied here: the claim under test is that switching
            # worktree narrows the tools to the new tree, not that `/tmp` is
            # reachable.
            from src.engine.files import paths
            from src.engine.files.models.file_tool_error import FileToolError

            monkeypatch.setattr(paths, '_POSIX_TEMP_DIRS', ())
            monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path / 'scratch'))

            with pytest.raises(FileToolError):
                recorder.tools['read_file'](path=str(repo / 'README.md'))

    _asyncio.run(inner())


def test_new_worktree_rejects_a_bad_name_without_switching(monkeypatch, tmp_path, repo):
    monkeypatch.chdir(repo)

    async def inner():
        app = _app(repo, tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.handle_command('/new', '--worktree nested/bad')
            await pilot.pause()

            assert Path.cwd() == repo
            assert app.current_workspace_label() == 'main'
            assert any('✖' in text for text in _texts(app))

    asyncio.run(inner())


def test_new_with_a_bad_flag_shows_the_usage(monkeypatch, tmp_path, repo):
    monkeypatch.chdir(repo)

    async def inner():
        app = _app(repo, tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            before = app._session.id
            await app.handle_command('/new', '--wt fix-login')
            await pilot.pause()

            assert app._session.id == before  # no session was created
            assert any('Usage: /new' in text for text in _texts(app))

    asyncio.run(inner())


def test_load_switches_to_the_workspace_of_the_session(monkeypatch, tmp_path, repo):
    monkeypatch.chdir(repo)

    async def inner():
        app = _app(repo, tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.handle_command('/new', '--worktree fix-login')
            await pilot.pause()
            worktree_session = app._session.id

            # Back to the main worktree, then load the worktree session from there.
            app.switch_workspace(str(repo))
            assert app.current_workspace_label() == 'main'

            # The picker is replaced by the choice itself: the test is about the
            # switch, not about driving a modal screen.
            async def _choose(_screen):
                return worktree_session

            monkeypatch.setattr(app, 'push_screen_wait', _choose)
            await app.handle_command('/load')
            await pilot.pause()

            assert app._session.id == worktree_session
            assert app.current_workspace_label() == 'fix-login'
            assert Path.cwd() == tmp_path / 'worktrees' / 'project' / 'fix-login'

    asyncio.run(inner())


def test_switching_clears_the_scope_and_the_loaded_skills(monkeypatch, tmp_path, repo):
    monkeypatch.chdir(repo)

    async def inner():
        from src.engine.context.scope import ScopeTracker

        scope = ScopeTracker(repo)
        app = _app(repo, tmp_path, scope=scope)
        async with app.run_test() as pilot:
            await pilot.pause()
            scope.observe('read_file', {'path': 'README.md'})
            app._loaded_skills.add('clean-code')

            await app.handle_command('/new', '--worktree clean-scope')
            await pilot.pause()

            assert scope.root == tmp_path / 'worktrees' / 'project' / 'clean-scope'
            assert scope.relative_scopes() == ('.',)
            assert app._loaded_skills.names() == ()

    asyncio.run(inner())


def test_load_across_worktrees_restores_the_workspace_and_the_model(monkeypatch, tmp_path, repo):
    """The two features have to compose: a session of another worktree keeps its model.

    Loading a session means two independent things at once — moving to the tree it
    belongs to and re-applying the model it was running on. If the switch dropped
    the model restore, a resumed conversation would silently change provider and
    price, which is exactly what the selection exists to prevent.
    """
    from src.engine.providers.domain.selection import Selection
    from src.engine.providers.snapshot import ModelSnapshot

    class FakeService:
        def __init__(self) -> None:
            self.applied: list[Selection] = []

        def snapshot(self) -> ModelSnapshot:
            return ModelSnapshot()

        def is_configured(self) -> bool:
            # A configured-enough router: the test is not about the catalogue.
            return False

        def apply(self, selection: Selection) -> ModelSnapshot:
            self.applied.append(selection)
            return ModelSnapshot()

    monkeypatch.chdir(repo)

    async def inner():
        app = _app(repo, tmp_path)
        service = FakeService()
        app._model_service = service
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.handle_command('/new', '--worktree composed')
            await pilot.pause()
            worktree_session = app._session.id

            selection = Selection(account_id='acc-1', model='anthropic/x', thinking='high')
            stored = app._store.load(worktree_session)
            stored.selection = selection
            app._store.save(stored)

            app.switch_workspace(str(repo))
            await pilot.pause()

            async def _choose(_screen):
                return worktree_session

            monkeypatch.setattr(app, 'push_screen_wait', _choose)
            await app.handle_command('/load')
            await pilot.pause()

            # Both halves of the load happened, and in the right order.
            assert app.current_workspace_label() == 'composed'
            assert Path.cwd() == tmp_path / 'worktrees' / 'project' / 'composed'
            assert service.applied == [selection]

    asyncio.run(inner())


def test_worktrees_dir_follows_the_environment(monkeypatch, tmp_path, repo):
    monkeypatch.setenv('SIGIL_WORKTREES_DIR', str(tmp_path / 'configured'))
    monkeypatch.chdir(repo)

    async def inner():
        app = _app(repo, tmp_path)
        # The manager resolves the directory lazily, so an unset override follows
        # whatever the environment says at the moment of creating the worktree.
        app._workspace_manager._worktrees_dir = None
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.handle_command('/new', '--worktree configured')
            await pilot.pause()

            assert Path.cwd() == tmp_path / 'configured' / 'project' / 'configured'

    asyncio.run(inner())
