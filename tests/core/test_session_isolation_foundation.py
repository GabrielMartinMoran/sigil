import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic_ai.models.test import TestModel
from pytest import MonkeyPatch

from src.core.bootstrap import Composition
from src.core.providers.bootstrap import ProviderPaths
from src.core.settings import SigilSettings


def _profile(root: Path, name: str, body: str, *, subagent: bool = False) -> None:
    directory = root / '.sigil' / 'agents'
    directory.mkdir(parents=True, exist_ok=True)
    role = 'role: subagent\n' if subagent else ''
    (directory / f'{name}.md').write_text(f'---\nname: {name}\ndescription: test\n{role}---\n{body}\n')


def _composition(root: Path, home: Path) -> Composition:
    return Composition(
        root, settings=SigilSettings(), model=TestModel(call_tools=[]), provider_paths=ProviderPaths(home)
    )


def test_project_profiles_refresh_and_cached_agents_are_invalidated_on_switch(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    first, second, outside = (tmp_path / name for name in ('first', 'second', 'outside'))
    for root in (first, second, outside):
        root.mkdir()
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    monkeypatch.chdir(outside)
    _profile(first, 'only-first', 'First agent')
    _profile(first, 'worker', 'First worker', subagent=True)
    _profile(second, 'worker', 'Second worker', subagent=True)
    composition = _composition(first, tmp_path / 'providers')
    try:
        registry = composition.agent_registry
        registry.activate('only-first')
        old_profile = registry.require_subagent('worker')
        old_agent = composition.factory.for_profile(old_profile)
        composition.workspace_manager.switch(second)
        assert registry is composition.agent_registry
        assert registry.active_name != 'only-first'
        assert registry.active is not None
        new_profile = registry.require_subagent('worker')
        assert new_profile.profile_file == second / '.sigil' / 'agents' / 'worker.md'
        assert composition.factory.for_profile(new_profile) is not old_agent
    finally:
        composition.close()


class _Recorder:
    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}

    def tool(self, func: Callable[..., Any], **kwargs: Any) -> Callable[..., Any]:
        self.tools[kwargs.get('name') or func.__name__] = func
        return func

    tool_plain = tool


def test_compositions_use_own_roots_after_another_switch(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    first, second, destination, outside = (tmp_path / name for name in ('first', 'second', 'destination', 'outside'))
    for root in (first, second, destination, outside):
        root.mkdir()
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    monkeypatch.chdir(outside)
    a = _composition(first, tmp_path / 'providers')
    b = _composition(second, tmp_path / 'providers')
    try:
        a_tools, b_tools = _Recorder(), _Recorder()
        a.install_tools(a_tools)
        b.install_tools(b_tools)
        a_tools.tools['write_file'](path='before.txt', content='first')
        b_tools.tools['write_file'](path='before.txt', content='second')
        assert a_tools.tools['read_file'](path='before.txt').content == 'first'
        assert b_tools.tools['read_file'](path='before.txt').content == 'second'
        assert (first / 'before.txt').read_text() == 'first'
        assert (second / 'before.txt').read_text() == 'second'
        assert Path.cwd() == outside
        a.workspace_manager.switch(destination)
        a_tools.tools['write_file'](path='after.txt', content='destination')
        b_tools.tools['write_file'](path='after.txt', content='second')
        assert (destination / 'after.txt').read_text() == 'destination'
        assert (second / 'after.txt').read_text() == 'second'
        assert Path.cwd() == outside
        assert a.workspace_manager.root == destination
    finally:
        a.close()
        b.close()


def test_permission_path_matching_uses_composition_root(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    from src.core.agents.profile.permissions import parse_permissions

    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.chdir(tmp_path)
    permissions = parse_permissions({'read': {'deny': ['private/*']}})
    absolute = str(root / 'private' / 'notes.txt')
    assert not permissions.permits('read_file', ('read',), (absolute,), kind='path', root=root)
    assert permissions.permits(
        'read_file', ('read',), (str(tmp_path / 'private' / 'notes.txt'),), kind='path', root=root
    )


def test_image_tools_follow_composition_root_and_switch(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    import asyncio
    import io

    from PIL import Image
    from pydantic_ai import RunContext, ToolReturn

    first, second, other, outside = (tmp_path / name for name in ('first', 'second', 'other', 'outside'))
    for root in (first, second, other, outside):
        root.mkdir()
    for root, size in ((first, 10), (second, 20), (other, 30)):
        image = Image.new('RGB', (size, size))
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        (root / 'image.png').write_bytes(buffer.getvalue())
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    monkeypatch.chdir(outside)
    a = _composition(first, tmp_path / 'providers')
    b = _composition(other, tmp_path / 'providers')
    try:
        a_tools, b_tools = _Recorder(), _Recorder()
        a.install_tools(a_tools)
        b.install_tools(b_tools)
        ctx = RunContext(deps=None, model=TestModel(), usage=None)

        def size(result: ToolReturn) -> tuple[int, int]:
            return Image.open(io.BytesIO(next(iter(result.content)).data)).size

        assert size(asyncio.run(a_tools.tools['view_image'](ctx, source='image.png'))) == (10, 10)
        assert size(asyncio.run(b_tools.tools['view_image'](ctx, source='image.png'))) == (30, 30)
        a.workspace_manager.switch(second)
        assert size(asyncio.run(a_tools.tools['view_image'](ctx, source='image.png'))) == (20, 20)
        assert size(asyncio.run(b_tools.tools['view_image'](ctx, source='image.png'))) == (30, 30)
    finally:
        a.close()
        b.close()


def test_conflict_guard_resolves_relative_and_absolute_paths_in_given_root(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    from src.core.files.conflicts import same_file_conflict
    from src.core.tooling.concurrency_context import ConcurrencyContext
    from src.core.tooling.sibling_call import SiblingCall

    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.chdir(tmp_path)
    ctx = ConcurrencyContext(
        'write_file', {'path': 'note.txt'}, None, (SiblingCall('patch_file', {'path': str(root / 'note.txt')}, None),)
    )
    assert same_file_conflict(ctx, root) is not None
    assert same_file_conflict(ctx, tmp_path) is None


def test_registry_preserves_active_and_explicit_roots(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    from src.core.agents.profile.agent_registry import AgentRegistry

    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    _profile(first, 'shared', 'first')
    _profile(second, 'shared', 'second')
    registry = AgentRegistry(cwd=first, active='shared')
    registry.rebind(second)
    assert registry.active_name == 'shared'
    assert registry.active.profile_file == second / '.sigil' / 'agents' / 'shared.md'
    explicit = AgentRegistry([('project', first / '.sigil' / 'agents')], active='shared')
    explicit.rebind(second)
    assert explicit.active.profile_file == first / '.sigil' / 'agents' / 'shared.md'


def test_composition_agent_enforces_project_relative_permission_after_switch(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    import asyncio

    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    first, second, outside = (tmp_path / name for name in ('first', 'second', 'outside'))
    for root in (first, second, outside):
        root.mkdir()
        (root / 'private').mkdir()
        (root / 'private' / 'notes.txt').write_text(root.name)
    for root in (first, second):
        _profile(root, 'limited', 'test')
        profile = root / '.sigil' / 'agents' / 'limited.md'
        profile.write_text(
            "---\nname: limited\ndescription: test\npermissions:\n  read:\n    deny: ['private/*']\n---\ntest\n"
        )
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    monkeypatch.chdir(outside)
    composition = _composition(first, tmp_path / 'providers')
    try:
        composition.agent_registry.activate('limited')
        target = [first / 'private' / 'notes.txt']

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
                return ModelResponse(parts=[TextPart('done')])
            return ModelResponse(parts=[ToolCallPart('read_file', {'path': str(target[0])})])

        composition._model_override = FunctionModel(model)
        agent = composition.rebuild_agent()
        composition.meter.set_window(100_000)

        def outcomes() -> list[ToolReturnPart]:
            result = asyncio.run(agent.run('read'))
            return [
                part for message in result.all_messages() for part in message.parts if isinstance(part, ToolReturnPart)
            ]

        assert outcomes()[-1].outcome == 'failed'
        composition.workspace_manager.switch(second)
        target[0] = second / 'private' / 'notes.txt'
        assert outcomes()[-1].outcome == 'failed'
        target[0] = first / 'private' / 'notes.txt'
        assert outcomes()[-1].outcome == 'success'
    finally:
        composition.close()


def test_shell_tools_use_each_composition_root(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    first, second, outside = (tmp_path / name for name in ('first', 'second', 'outside'))
    for root in (first, second, outside):
        root.mkdir()
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    monkeypatch.chdir(outside)
    a, b = _composition(first, tmp_path / 'providers'), _composition(second, tmp_path / 'providers')
    try:
        a_tools, b_tools = _Recorder(), _Recorder()
        a.install_tools(a_tools)
        b.install_tools(b_tools)
        result_a = asyncio.run(a_tools.tools['run'](cmds=['pwd']))
        result_b = asyncio.run(b_tools.tools['run'](cmds=['pwd']))
        assert result_a[0].stdout.strip() == str(first)
        assert result_b[0].stdout.strip() == str(second)
        assert Path.cwd() == outside
    finally:
        a.close()
        b.close()


def test_shell_cancellation_is_selective_during_spawn(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    from src.core.shell import runner

    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    a, b = _composition(first, tmp_path / 'providers'), _composition(second, tmp_path / 'providers')
    spawned_first = threading.Event()
    release_first = threading.Event()
    spawned_second = threading.Event()
    cancellation_started = threading.Event()
    cancellation_attempted_lock = threading.Event()
    processes: dict[str, Any] = {}
    original_spawn = runner.spawn

    def paused_spawn(cmd: str, *, cwd: str | None, extra_env: dict[str, str] | None) -> Any:
        process = original_spawn(cmd, cwd=cwd, extra_env=extra_env)
        processes[str(cwd)] = process
        if cwd == str(first):
            spawned_first.set()
            assert release_first.wait(10)
        else:
            spawned_second.set()
        return process

    monkeypatch.setattr(runner, 'spawn', paused_spawn)
    a_tools, b_tools = _Recorder(), _Recorder()
    a.install_tools(a_tools)
    b.install_tools(b_tools)
    command = "exec python -c 'import signal; signal.pause()'"
    try:
        owner = a.shell_commands

        class ObservedLock:
            def __init__(self) -> None:
                self.lock = threading.Lock()

            def __enter__(self) -> None:
                if cancellation_started.is_set():
                    cancellation_attempted_lock.set()
                self.lock.acquire()

            def __exit__(self, *args: object) -> None:
                self.lock.release()

        owner._lock = ObservedLock()
        with ThreadPoolExecutor(max_workers=3) as pool:
            first_run = pool.submit(lambda: asyncio.run(a_tools.tools['run'](cmds=[command])))
            assert spawned_first.wait(10)
            second_run = pool.submit(lambda: asyncio.run(b_tools.tools['run'](cmds=[command])))
            assert spawned_second.wait(10)

            def cancel_first() -> int:
                cancellation_started.set()
                return owner.kill_all()

            cancelled = pool.submit(cancel_first)
            assert cancellation_started.wait(10)
            assert cancellation_attempted_lock.wait(10)
            release_first.set()
            cancelled.result(timeout=10)
            assert first_run.result(timeout=10)[0].returncode != 0
            assert processes[str(second)].poll() is None
            b.shell_commands.kill_all()
            assert second_run.result(timeout=10)[0].returncode != 0
    finally:
        release_first.set()
        for process in processes.values():
            if process.poll() is None:
                from src.core.shell.process import kill_process_group

                kill_process_group(process)
        a.close()
        b.close()


def test_shell_cancel_stops_child_group_without_stopping_other_composition(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    import os
    import select
    import shlex
    import sys

    from src.core.shell import runner
    from src.core.shell.process import kill_process_group

    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    fifo = tmp_path / 'ready'
    os.mkfifo(fifo)
    ready_fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    a, b = _composition(first, tmp_path / 'providers'), _composition(second, tmp_path / 'providers')
    first_tools, second_tools = _Recorder(), _Recorder()
    a.install_tools(first_tools)
    b.install_tools(second_tools)
    processes: dict[str, Any] = {}
    second_spawned = threading.Event()
    original_spawn = runner.spawn

    def observe_spawn(cmd: str, *, cwd: str | None, extra_env: dict[str, str] | None) -> Any:
        process = original_spawn(cmd, cwd=cwd, extra_env=extra_env)
        processes[str(cwd)] = process
        if cwd == str(second):
            second_spawned.set()
        return process

    monkeypatch.setattr(runner, 'spawn', observe_spawn)
    program = (
        'import os,signal; '
        'pid=os.fork(); '
        f'fd=os.open({str(fifo)!r},os.O_WRONLY) if pid==0 else None; '
        "os.write(fd,('%s %s\\n' % (os.getpid(),os.getpgrp())).encode()) if pid==0 else None; "
        'os.close(fd) if pid==0 else None; '
        'signal.pause()'
    )
    first_command = f'exec {shlex.quote(sys.executable)} -c {shlex.quote(program)}'
    second_command = f'exec {shlex.quote(sys.executable)} -c {shlex.quote("import signal; signal.pause()")}'
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_run = pool.submit(lambda: asyncio.run(first_tools.tools['run'](cmds=[first_command])))
            second_run = pool.submit(lambda: asyncio.run(second_tools.tools['run'](cmds=[second_command])))
            try:
                assert select.select([ready_fd], [], [], 10)[0], 'child did not become ready'
                child_pid, child_group = map(int, os.read(ready_fd, 128).split())
                assert child_pid != processes[str(first)].pid
                assert child_group == processes[str(first)].pid
                assert second_spawned.wait(10)
                assert a.shell_commands.kill_all() == 1
                result = first_run.result(timeout=10)[0]
                assert result.returncode != 0
                assert not result.timed_out
                # The child inherited both output pipes: drain cannot finish until it exits.
                assert processes[str(second)].poll() is None
            finally:
                for process in list(processes.values()):
                    kill_process_group(process)
                a.shell_commands.kill_all()
                b.shell_commands.kill_all()
            assert second_run.result(timeout=10)[0].returncode != 0
    finally:
        os.close(ready_fd)
        a.close()
        b.close()


def test_binding_restores_profile_prompt_and_persisted_fallback(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    _profile(tmp_path, 'reviewer', 'Review with care.')
    _profile(tmp_path, 'worker', 'Only delegated tasks.', subagent=True)
    composition = _composition(tmp_path, tmp_path / 'providers')
    try:
        session = composition.store.create_session()
        session.agent = 'reviewer'
        composition.store.save(session)
        composition.bind_session(session, model=False)
        assert composition.agent_registry.active_name == 'reviewer'
        assert 'Review with care.' in composition.agent_registry.active_body()
        assert composition.agent_trees.active.root.name == 'reviewer'
        for invalid in ('removed-profile', 'worker'):
            session.agent = invalid
            composition.store.save(session)
            composition.agent_registry.activate('reviewer')
            composition.bind_session(session, model=False)
            assert composition.agent_registry.active_name == 'wizard'
            assert session.agent == 'wizard'
            assert composition.agent_trees.active.root.name == 'wizard'
            assert composition.store.load(session.id).agent == 'wizard'
    finally:
        composition.close()


def test_binding_restricted_profile_denies_real_patch_calls(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    _profile(tmp_path, 'limited', 'Change only authorized files.')
    (tmp_path / '.sigil/agents/limited.md').write_text(
        '---\nname: limited\ndescription: restricted\npermissions:\n'
        "  write:\n    allow: ['allowed/*']\n---\nChange only authorized files.\n"
    )
    for directory in ('src', 'allowed'):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / 'note.txt').write_text('before')
    composition = _composition(tmp_path, tmp_path / 'providers')
    try:
        target = ['src/note.txt']

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
                return ModelResponse(parts=[TextPart('done')])
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        'patch_file',
                        {
                            'path': target[0],
                            'old_string': 'before',
                            'new_string': 'after',
                        },
                    )
                ]
            )

        composition._model_override = FunctionModel(model)
        agent = composition.rebuild_agent()
        session = composition.store.create_session()
        session.agent = 'limited'
        composition.store.save(session)
        composition.bind_session(session, model=False)
        composition.meter.set_window(100_000)

        def outcome() -> str:
            result = asyncio.run(agent.run('patch'))
            return next(
                part.outcome
                for message in result.all_messages()
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            )

        assert outcome() == 'failed'
        assert (tmp_path / 'src/note.txt').read_text() == 'before'
        target[0] = 'allowed/note.txt'
        assert outcome() == 'success'
        assert (tmp_path / 'allowed/note.txt').read_text() == 'after'
    finally:
        composition.close()


def test_profile_selection_is_persisted_before_another_turn(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    _profile(tmp_path, 'reviewer', 'Review.')
    composition = _composition(tmp_path, tmp_path / 'providers')
    try:
        session = composition.store.create_session()
        session.agent = 'wizard'
        composition.store.save(session)
        composition.bind_session(session, model=False)
        composition.apply_agent('reviewer')
        assert composition.store.load(session.id).agent == 'reviewer'
    finally:
        composition.close()


def test_global_profiles_survive_binding_sessions_in_different_projects(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    home = tmp_path / 'home'
    global_profiles = home / 'agents'
    global_profiles.mkdir(parents=True)
    (global_profiles / 'shared.md').write_text(
        '---\nname: shared\ndescription: Global profile.\n---\nGlobal instructions.'
    )
    monkeypatch.setenv('SIGIL_HOME', str(home))
    first, second, outside = (tmp_path / name for name in ('first', 'second', 'outside'))
    for root in (first, second, outside):
        root.mkdir()
    _profile(first, 'shared', 'Project override.')
    _profile(second, 'second-only', 'Second project.')
    monkeypatch.chdir(outside)
    composition = _composition(first, tmp_path / 'providers')
    try:
        assert composition.agent_registry.get('shared').source == 'project'
        composition.workspace_manager.switch(second)
        session = composition.store.create_session()
        session.agent = 'shared'
        composition.store.save(session)
        composition.bind_session(session, model=False)
        assert composition.agent_registry.active_name == 'shared'
        assert composition.agent_registry.active.source == 'global'
        assert composition.agent_registry.active_body() == 'Global instructions.'
        assert composition.agent_registry.get('second-only') is not None
        assert composition.agent_trees.active.root.name == 'shared'
    finally:
        composition.close()


def _write_skill(root: Path, name: str) -> None:
    directory = root / '.agents' / 'skills' / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: test skill\n---\nBody\n')


def test_two_executions_resolve_their_own_roots_without_moving_the_process(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    from src.core.files.paths import execution_workspace

    first, second, outside = (tmp_path / name for name in ('first', 'second', 'outside'))
    for root in (first, second, outside):
        root.mkdir()
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    monkeypatch.chdir(outside)
    a = _composition(first, tmp_path / 'providers')
    b = _composition(second, tmp_path / 'providers')
    try:
        a_tools, b_tools = _Recorder(), _Recorder()
        a.install_tools(a_tools)
        b.install_tools(b_tools)
        # Outside any run the composition's own workspace is the fallback.
        assert a.execution_root() == first
        assert b.execution_root() == second

        def write(root: Path, tools: _Recorder) -> None:
            with execution_workspace(root):
                tools.tools['write_file'](path='note.txt', content=root.name)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda pair: write(*pair), ((first, a_tools), (second, b_tools))))

        assert (first / 'note.txt').read_text() == 'first'
        assert (second / 'note.txt').read_text() == 'second'
        assert Path.cwd() == outside
    finally:
        a.close()
        b.close()


def test_child_tasks_and_threads_keep_the_spawning_root_after_the_parent_leaves(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    from src.core.files.paths import execution_workspace, workspace_root

    first, second, outside = (tmp_path / name for name in ('first', 'second', 'outside'))
    for root in (first, second, outside):
        root.mkdir()
    monkeypatch.chdir(outside)

    async def scenario() -> tuple[Path, Path]:
        started = asyncio.Event()
        release = asyncio.Event()

        async def child() -> Path:
            started.set()
            await release.wait()
            return workspace_root()

        with execution_workspace(first):
            task = asyncio.get_running_loop().create_task(child())
            await started.wait()
            threaded = await asyncio.to_thread(workspace_root)
        with execution_workspace(second):
            release.set()
            return await task, threaded

    child_root, threaded_root = asyncio.run(scenario())

    assert child_root == first.resolve()
    assert threaded_root == first.resolve()
    assert Path.cwd() == outside


def test_a_cancelled_execution_restores_the_previous_root(tmp_path: Path) -> None:
    from src.core.files.paths import execution_workspace, workspace_root

    outer, bound = tmp_path / 'outer', tmp_path / 'bound'
    outer.mkdir()
    bound.mkdir()

    async def scenario() -> Path:
        with execution_workspace(outer):

            async def child() -> None:
                with execution_workspace(bound):
                    await asyncio.sleep(10)

            task = asyncio.get_running_loop().create_task(child())
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return workspace_root()

    assert asyncio.run(scenario()) == outer.resolve()


def test_registered_tool_callbacks_follow_the_bound_root(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """File, conflict, shell and image boundaries resolve the run's root."""
    import asyncio
    import io

    from PIL import Image
    from pydantic_ai import RunContext
    from pydantic_ai.models.test import TestModel

    from src.core.files.conflicts import same_file_conflict
    from src.core.files.paths import execution_workspace
    from src.core.tooling.concurrency_context import ConcurrencyContext
    from src.core.tooling.sibling_call import SiblingCall

    composed, bound, outside = (tmp_path / name for name in ('composed', 'bound', 'outside'))
    for root in (composed, bound, outside):
        root.mkdir()
    buffer = io.BytesIO()
    Image.new('RGB', (12, 12)).save(buffer, format='PNG')
    (bound / 'image.png').write_bytes(buffer.getvalue())
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    monkeypatch.chdir(outside)
    composition = _composition(composed, tmp_path / 'providers')
    try:
        tools = _Recorder()
        composition.install_tools(tools)
        ctx = RunContext(deps=None, model=TestModel(), usage=None)

        with execution_workspace(bound):
            tools.tools['write_file'](path='note.txt', content='bound')
            assert tools.tools['read_file'](path='note.txt').content == 'bound'
            assert (bound / 'note.txt').read_text() == 'bound'
            conflict = ConcurrencyContext(
                'write_file',
                {'path': 'note.txt'},
                None,
                (SiblingCall('patch_file', {'path': str(bound / 'note.txt')}, None),),
            )
            assert same_file_conflict(conflict, composition.execution_root()) is not None
            assert Path(asyncio.run(tools.tools['run'](['pwd']))[0].stdout.strip()).resolve() == bound.resolve()
            viewed = asyncio.run(tools.tools['view_image'](ctx, source='image.png'))
            size = Image.open(io.BytesIO(next(iter(viewed.content)).data)).size

        assert size == (12, 12)
        assert Path.cwd() == outside
        assert not (composed / 'note.txt').exists()
    finally:
        composition.close()


def test_default_skill_discovery_uses_the_composition_root(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    first, outside, custom = (tmp_path / name for name in ('first', 'outside', 'custom'))
    for root in (first, outside, custom):
        root.mkdir()
    _write_skill(first, 'project-skill')
    _write_skill(custom, 'custom-skill')
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    monkeypatch.chdir(outside)
    composition = _composition(first, tmp_path / 'providers')
    try:
        names = [skill.name for skill in composition.skill_registry.list()]
        assert 'project-skill' in names
        assert composition.skill_registry.roots[0][1] == first / '.agents' / 'skills'
    finally:
        composition.close()

    monkeypatch.setenv('SIGIL_SKILLS_DIRS', str(custom / '.agents' / 'skills'))
    explicit = _composition(first, tmp_path / 'providers')
    try:
        names = [skill.name for skill in explicit.skill_registry.list()]
        assert 'custom-skill' in names
        assert 'project-skill' not in names
    finally:
        explicit.close()
