import asyncio
from pathlib import Path

from src.engine.extensions.manager import ExtensionManager


def extension(root: Path, name: str, source: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / 'tools.py').write_text(source)
    return directory


def test_untrusted_project_is_not_imported(tmp_path: Path) -> None:
    project = tmp_path / 'workspace'
    project.mkdir()
    marker = tmp_path / 'executed'
    extension(project / '.sigil' / 'tools', 'unsafe', f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
    manager = ExtensionManager(project, global_dir=tmp_path / 'global')
    asyncio.run(manager.load())
    assert not marker.exists()
    assert manager.errors and 'trust' in manager.errors[0].lower()


def test_lifecycle_and_tools_are_loaded(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    marker = tmp_path / 'lifecycle'
    extension(
        workspace / '.sigil' / 'tools',
        'example',
        f"""from sigil_tools import tool
async def on_load(ctx):
    open({str(marker)!r}, 'w').write('loaded')
async def on_unload(ctx):
    open({str(marker)!r}, 'a').write(':unloaded')
@tool
async def hello(name: str) -> str:
    return name
""",
    )
    manager = ExtensionManager(workspace, global_dir=tmp_path / 'global', trust_project=True)

    async def run() -> None:
        await manager.load()
        assert [item.name for item in manager.tools] == ['hello']
        assert marker.read_text() == 'loaded'
        await manager.close()

    asyncio.run(run())
    assert marker.read_text() == 'loaded:unloaded'


def test_broken_extension_does_not_stop_other_extensions(tmp_path: Path) -> None:
    extension(tmp_path / 'global', 'broken', 'raise RuntimeError("bad extension")')
    extension(
        tmp_path / 'global',
        'good',
        'from sigil_tools import tool\n@tool\nasync def hello() -> str:\n    return "hello"\n',
    )
    manager = ExtensionManager(tmp_path, global_dir=tmp_path / 'global')
    asyncio.run(manager.load())
    assert [item.name for item in manager.tools] == ['hello']
    assert any('bad extension' in error for error in manager.errors)


def test_failed_load_cleans_partial_state_and_skips_tool(tmp_path: Path) -> None:
    marker = tmp_path / 'cleanup'
    extension(
        tmp_path / 'global',
        'broken',
        f"""from pathlib import Path
from sigil_tools import tool
async def on_load(ctx):
    raise RuntimeError('startup failed')
async def on_unload(ctx):
    Path({str(marker)!r}).touch()
@tool
async def hidden() -> str:
    return 'hidden'
""",
    )
    manager = ExtensionManager(tmp_path, global_dir=tmp_path / 'global')
    asyncio.run(manager.load())
    assert not manager.tools
    assert marker.exists()
    assert any('startup failed' in error for error in manager.errors)


def test_invalid_hook_is_reported(tmp_path: Path) -> None:
    extension(tmp_path / 'global', 'bad', 'def on_load(ctx):\n    pass\n')
    manager = ExtensionManager(tmp_path, global_dir=tmp_path / 'global')
    asyncio.run(manager.load())
    assert any('on_load must be declared with async def' in error for error in manager.errors)


def test_project_tool_overrides_global_tool(tmp_path: Path) -> None:
    source = 'from sigil_tools import tool\n@tool\nasync def hello() -> str:\n    return "hello"\n'
    extension(tmp_path / 'global', 'global', source)
    workspace = tmp_path / 'workspace'
    extension(workspace / '.sigil' / 'tools', 'project', source)
    manager = ExtensionManager(workspace, global_dir=tmp_path / 'global', trust_project=True)
    asyncio.run(manager.load())
    assert len(manager.tools) == 1
    assert manager.tools[0].project


def test_cancelled_load_cleans_partial_resources(tmp_path: Path) -> None:
    marker = tmp_path / 'cleanup-cancel'
    extension(
        tmp_path / 'global',
        'cancelled',
        f"""import asyncio
from pathlib import Path
async def on_load(ctx):
    raise asyncio.CancelledError()
async def on_unload(ctx):
    Path({str(marker)!r}).touch()
""",
    )
    manager = ExtensionManager(tmp_path, global_dir=tmp_path / 'global')
    import pytest

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager.load())
    assert marker.exists()


def test_relative_imports_and_optional_hooks(tmp_path: Path) -> None:
    directory = extension(
        tmp_path / 'global',
        'relative',
        """from .helper import message
from sigil_tools import tool
@tool
async def example() -> str:
    return message
""",
    )
    (directory / 'helper.py').write_text('message = "hello"\n')
    manager = ExtensionManager(tmp_path, global_dir=tmp_path / 'global')

    async def run() -> None:
        await manager.load()
        assert await manager.tools[0].declaration.function() == 'hello'
        await manager.close()
        await manager.close()
        assert not manager.errors

    asyncio.run(run())


def test_hook_timeout_diagnoses_failure_and_cleans_up(tmp_path: Path) -> None:
    marker = tmp_path / 'cleaned'
    extension(
        tmp_path / 'global',
        'timeout',
        f"""import asyncio
from pathlib import Path
async def on_load(ctx):
    await asyncio.Event().wait()
async def on_unload(ctx):
    Path({str(marker)!r}).touch()
""",
    )
    manager = ExtensionManager(tmp_path, global_dir=tmp_path / 'global')
    manager.hook_timeout = 0.01
    asyncio.run(manager.load())
    assert not manager.tools
    assert marker.exists()
    assert any('TimeoutError' in error for error in manager.errors)


def test_decorated_lifecycle_hook_is_rejected(tmp_path: Path) -> None:
    extension(
        tmp_path / 'global',
        'reserved',
        """from sigil_tools import tool
@tool
async def on_load(ctx):
    pass
""",
    )
    manager = ExtensionManager(tmp_path, global_dir=tmp_path / 'global')
    asyncio.run(manager.load())
    assert not manager.tools
    assert any('reserved' in error for error in manager.errors)
