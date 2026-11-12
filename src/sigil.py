"""Sigil entry point: builds the agent, the tools, the session and the UI."""

from __future__ import annotations

from dotenv import load_dotenv

from src.engine.bootstrap import Composition
from src.engine.preferences import PreferencesStore
from src.engine.settings import load_settings
from src.engine.themes import ThemeRegistry
from src.ui.app import SigilApp


def run() -> None:
    # The only place the process environment is read. Everything below receives
    # values, so importing any module cannot freeze a setting or pull `.env` in.
    load_dotenv()
    settings = load_settings()

    composition = Composition(settings=settings)

    app = SigilApp(
        agent=composition.agent,
        store=composition.store,
        session=composition.session,
        on_bind_session=composition.bind_session,
        on_agent_selected=composition.select_agent_model,
        settings=settings,
        model_service=composition.model_service,
        login_flow=composition.login_flow,
        tool_logger=composition.tool_logger,
        mcp_manager=composition.mcp,
        startup_notices=(*composition.mcp_config.errors,),
        mcp_loader=composition.mcp_loader(),
        question_hub=composition.question_hub,
        roadmap_store=composition.roadmap_store,
        rollback=composition.rollback,
        model_retry=composition.model_retry,
        meter=composition.meter,
        context_capability=composition.context_capability,
        scope=composition.scope,
        workspace=composition.workspace,
        workspace_manager=composition.workspace_manager,
        # UI preferences live outside the session: they are the user's, not the
        # conversation's, and they have to be read before the first paint.
        preferences_store=PreferencesStore(),
        themes=ThemeRegistry.load(),
        agent_registry=composition.agent_registry,
        agent_tree=composition.agent_trees,
        summoner=composition.summoner,
        extensions=composition.extensions,
        cancel_shell=composition.shell_commands.kill_all,
    )
    composition.extension_notice = lambda text: app.notify(text, severity='warning', timeout=15)
    app.run()
