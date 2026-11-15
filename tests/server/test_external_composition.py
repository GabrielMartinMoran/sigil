"""Local extensions use the real composition, permissions and runtime context."""

import asyncio
from pathlib import Path

from src.engine.bootstrap import Composition
from src.engine.providers.bootstrap import ProviderPaths
from src.engine.settings import load_settings


def test_server_lifespan_unloads_extensions(tmp_path: Path, monkeypatch) -> None:
    from pydantic_ai.models.test import TestModel

    from src.server.app import create_app
    from src.server.config import ServerConfig
    from src.server.events import EventHub
    from src.server.runtime import ServerRuntime

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    directory = tmp_path / 'home' / 'tools' / 'lifecycle'
    directory.mkdir(parents=True)
    marker = tmp_path / 'unloaded'
    (directory / 'tools.py').write_text(f"""from pathlib import Path
async def on_unload(ctx):
    Path({str(marker)!r}).touch()
""")
    composition = Composition(
        tmp_path,
        settings=load_settings(),
        model=TestModel(call_tools=[]),
        provider_paths=ProviderPaths(tmp_path / 'providers'),
    )
    runtime = ServerRuntime(composition, EventHub())
    app = create_app(runtime, ServerConfig(password='test-password'), dist=tmp_path / 'missing')

    async def run() -> None:
        async with app.router.lifespan_context(app):
            await composition.extensions._load()
            assert not marker.exists()
        assert marker.exists()

    asyncio.run(run())
