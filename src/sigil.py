"""Sigil entry point: the terminal attaches to the service that executes.

One process owns the conversation: it holds the session, the durable queue and the
turn. This module is how the terminal reaches it — find a live service (starting one
only when there is none), read what it projects, and hand the window a client to talk
to it with.

Two refusals are worth writing down, because both are easy to undo by accident.

**No engine is composed here.** `Composition` builds the agent, the tools and the
model router — everything a turn needs — and the service already has them. A terminal
that built its own would be a second executor on one session, which is the failure
this whole increment exists to remove. Nothing in this file imports `Composition`, and
`SigilApp` raises when it is given a client *and* in-process collaborators, so the
in-process turn path cannot be reached from here even by mistake.

**A failure to attach is a sentence, not a traceback.** The launcher already reports a
typed reason — still starting, died, timed out — and it leaves as advice with a
non-zero exit code. A stack trace here would tell the user nothing they can act on.

What is still built locally is exactly the collaborators whose mixins have not moved
yet (`provider`, `agent`, `rollback`, `tree`, `commands`). They are cheap, they read
the same files the service reads, and none of them can execute a turn. What cannot be
built without composing an engine — the model service and the store that owns the
conversation row — is deliberately absent: an attached terminal paints the model and
the transcript from the service's state, and those panels arrive in their own cuts
rather than as a second engine started from the terminal.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import rich
from dotenv import load_dotenv

from src.engine.agents.profile.registry import AgentRegistry
from src.engine.preferences import PreferencesStore
from src.engine.roadmap.stores import RoadmapStores
from src.engine.sessions.session_store import Session
from src.engine.settings import SigilSettings, load_settings
from src.engine.themes import ThemeRegistry
from src.ui.app import SigilApp
from src.ui.service_client import ServiceClient, ServiceClientError, ServiceUnavailable


def run() -> None:
    """Attaches to the service and runs the terminal until the user leaves."""
    # The only place the process environment is read. Everything below receives
    # values, so importing any module cannot freeze a setting or pull `.env` in.
    load_dotenv()
    settings = load_settings()
    asyncio.run(_attached(settings))


async def _attached(settings: SigilSettings) -> None:
    """One event loop for the whole session: the client, the screen and the stream.

    The client is built on this loop and closed on it. That is the reason this is a
    coroutine and `run` is not: an `httpx` connection pool is bound to the loop that
    created it, and a client connected in one loop and used in another fails on the
    first request.
    """
    try:
        client = await ServiceClient.connect()
    except ServiceUnavailable as exc:
        _refuse(str(exc))
        return
    try:
        try:
            state = await client.state()
        except ServiceClientError as exc:
            _refuse(str(exc))
            return
        await _app(client, state, settings).run_async()
    finally:
        await client.aclose()


def _app(client: ServiceClient, state: dict[str, Any], settings: SigilSettings) -> SigilApp:
    """The terminal, built over a client and nothing that can run a turn.

    The collaborators passed beside the client are the ones the unmigrated mixins need
    to paint what they paint locally: preferences and themes are this client's own,
    and the agent profiles and the roadmap are files and rows it reads the same way the
    service does. None of them executes anything.

    The three callbacks the in-process composition supplies have no counterpart here
    on purpose. `on_bind_session` and `on_agent_selected` exist to resolve a model
    inside the process that owns the session, and in an attached terminal that process
    is the service: it resolves on bind and on `POST /api/agent`, and the terminal
    paints the snapshot it sends back. `model_defaults_state` and
    `save_model_default` write the defaults files, which the service owns too; no
    endpoint projects them yet, so `/default-models` reports that this client cannot
    edit them instead of writing a second copy of the truth.
    """
    workspace = _workspace(state)
    session = _session(state)
    return SigilApp(
        client=client,
        session=session,
        settings=settings,
        preferences_store=PreferencesStore(),
        themes=ThemeRegistry.load(),
        agent_registry=AgentRegistry(cwd=workspace),
        roadmap_store=RoadmapStores(session.id or None),
    )


def _session(state: dict[str, Any]) -> Session:
    """The conversation the window is showing, as the service projects it.

    A reading, not a write: the row belongs to the service. `SigilApp` needs a session
    to name the window and to know which conversation it is on, and the transcript is
    not carried here — the state and the stream describe it. Until the session cut
    lands, this projection is what the title bar paints.
    """
    view = state.get('session') or {}
    return Session(
        id=str(view.get('id') or ''),
        cwd=_workspace(state),
        title=view.get('title'),
        agent=str(view.get('agent') or ''),
        allow_external_files=bool(view.get('external_files')),
        mcp_overrides=dict(view.get('mcp_overrides') or {}),
    )


def _workspace(state: dict[str, Any]) -> str:
    """The directory the service is working in, for the panels that read files."""
    view = state.get('session') or {}
    return str(view.get('workspace') or Path.cwd())


def _refuse(message: str) -> None:
    """Says why the terminal cannot attach, and what to do about it."""
    rich.print(f'[bold red]Sigil cannot attach to its service:[/bold red] {message}')
    rich.print('Start it with [bold]sigil server[/bold], or check that it is still running.')
    raise SystemExit(1)
