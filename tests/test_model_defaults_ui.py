"""The screen collects a target and a destination; nothing else happens there.

Every assertion goes through real key handling on a minimal host, because the two
things that break by hand are the ones a handler-only test cannot see: `Tab` never
reaching the screen because the list has the focus, and the destination not
following the row the user moved to.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from textual.app import App
from textual.widgets import OptionList

from src.engine.agents.model_config import AgentModelConfig
from src.engine.agents.model_defaults import ConfigScope, read_defaults, save_default
from src.engine.agents.profile.models.agent_profile import AgentProfile
from src.engine.agents.profile.models.agent_role import AgentRole
from src.ui.provider_mixin import defaults_rows
from src.ui.widgets.model_defaults_screen import (
    DefaultsChoice,
    DefaultsRow,
    ModelDefaultsScreen,
)
from tests.ui_helpers import render_text

PROJECT_MODEL = AgentModelConfig('work', 'openai/gpt-5', 'high')
USER_MODEL = AgentModelConfig('personal', 'other/model', 'low')


def _files(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / 'project.json', tmp_path / 'user.json'


def _profile(name: str, role: AgentRole) -> AgentProfile:
    """A profile with only what the list reads: name, role and where it came from."""
    path = Path('/profiles') / f'{name}.md'
    return AgentProfile(
        name=name,
        description=f'{name} profile',
        path=path.parent,
        profile_file=path,
        source='builtin',
        role=role,
    )


def _state(tmp_path: Path, project: dict | None = None, user: dict | None = None):
    project_path, user_path = _files(tmp_path)
    for path, payload in ((project_path, project), (user_path, user)):
        if payload is not None:
            path.write_text(json.dumps(payload), encoding='utf-8')
    return read_defaults(paths=(project_path, user_path))


def _rows(state) -> list[DefaultsRow]:
    """The two rows these tests need: the default, and one agent."""
    return [
        DefaultsRow(
            target=None,
            label='Default for new conversations',
            summary='not set' if state.effective(None) is None else 'set',
            sources=state.sources(None),
        ),
        DefaultsRow(target='wizard', label='Wizard', summary='', sources=state.sources('wizard')),
    ]


def _press(screen: ModelDefaultsScreen, keys: tuple[str, ...]):
    """Pushes the screen, presses ``keys`` and returns what it dismissed with."""

    async def inner():
        app = App()
        result: dict[str, object] = {}
        async with app.run_test() as pilot:
            app.push_screen(screen, lambda value: result.setdefault('value', value))
            await pilot.pause()
            await pilot.press(*keys)
            await pilot.pause()
        return result.get('value')

    return asyncio.run(inner())


def _status(screen: ModelDefaultsScreen) -> str:
    return render_text(screen.query_one('#defaults-status').render())


def _mounted(screen: ModelDefaultsScreen) -> dict[str, str]:
    """Mounts ``screen`` in a minimal host and returns the text it paints."""

    async def inner() -> dict[str, str]:
        app = App()
        painted: dict[str, str] = {}
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            options = screen.query_one(OptionList)
            painted['list'] = '\n'.join(render_text(option.prompt) for option in options.options)
            painted['status'] = _status(screen)
        return painted

    return asyncio.run(inner())


# --- the destination ---------------------------------------------------------


def test_should_start_the_destination_where_the_entry_lives(tmp_path: Path):
    state = _state(tmp_path, project={'agents': {'wizard': {'account': 'w', 'model': 'm', 'effort': 'low'}}})
    screen = ModelDefaultsScreen(_rows(state), state)

    async def inner() -> None:
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            # The default row is not set anywhere, so it starts on the user's file.
            assert 'Destination: global' in _status(screen)
            await pilot.press('down')
            await pilot.pause()
            # The agent row is set by the project, so the destination follows it.
            assert 'Destination: project' in _status(screen)
            await pilot.press('tab')
            await pilot.pause()
            assert 'Destination: global' in _status(screen)

    asyncio.run(inner())


def test_should_return_the_row_and_the_destination(tmp_path: Path):
    state = _state(tmp_path, user={'default_model': {'account': 'w', 'model': 'm', 'effort': 'low'}})
    screen = ModelDefaultsScreen(_rows(state), state)

    choice = _press(screen, ('enter',))

    assert isinstance(choice, DefaultsChoice)
    assert choice.row.target is None
    assert choice.scope is ConfigScope.GLOBAL
    assert choice.clear is False


def test_should_only_clear_where_the_entry_is_written(tmp_path: Path):
    state = _state(tmp_path, user={'agents': {'wizard': {'account': 'w', 'model': 'm', 'effort': 'low'}}})
    screen = ModelDefaultsScreen(_rows(state), state)

    async def inner() -> None:
        async with App().run_test() as pilot:
            app = pilot.app
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press('down')
            await pilot.pause()
            assert 'Destination: global' in _status(screen)
            await pilot.press('tab')
            await pilot.pause()
            assert 'Destination: project' in _status(screen)
            await pilot.press('d')
            await pilot.pause()
            # The project file does not set this agent, so there is nothing to clear
            # there and the screen says so instead of dismissing.
            assert app.screen is screen
            assert 'Nothing to clear' in _status(screen)

    asyncio.run(inner())

    choice = _press(ModelDefaultsScreen(_rows(state), state), ('down', 'd'))

    assert isinstance(choice, DefaultsChoice)
    assert choice.row.target == 'wizard'
    assert choice.clear is True


# --- what the rows say -------------------------------------------------------


def test_should_show_which_file_sets_each_entry(tmp_path: Path):
    state = _state(
        tmp_path,
        project={'agents': {'wizard': {'account': 'p', 'model': 'p/m', 'effort': 'high'}}},
        user={'agents': {'wizard': {'account': 'u', 'model': 'u/m', 'effort': 'low'}}},
    )

    painted = _mounted(ModelDefaultsScreen(_rows(state), state))

    assert 'project, overrides global' in painted['list']
    assert 'Default for new conversations' in painted['list']


def test_should_show_an_invalid_entry_as_invalid(tmp_path: Path):
    state = _state(tmp_path, project={'agents': {'wizard': {'account': 'a', 'model': 'm', 'effort': 'typo'}}})
    row = DefaultsRow(
        target='wizard',
        label='Wizard',
        summary='invalid entry',
        sources=state.sources('wizard'),
        invalid=True,
    )

    text = _mounted(ModelDefaultsScreen([row], state))['list']

    # The file is named too: it is the one the user has to clear.
    assert 'project, invalid' in text


def test_should_lock_the_destination_when_sigil_config_replaces_both(tmp_path: Path, monkeypatch):
    fixed = tmp_path / 'explicit.json'
    monkeypatch.setenv('SIGIL_CONFIG', str(fixed))
    state = read_defaults()
    screen = ModelDefaultsScreen(_rows(state), state)

    async def inner() -> None:
        async with App().run_test() as pilot:
            pilot.app.push_screen(screen)
            await pilot.pause()
            assert 'SIGIL_CONFIG' in _status(screen)
            await pilot.press('tab')
            await pilot.pause()
            assert 'SIGIL_CONFIG' in _status(screen)

    asyncio.run(inner())


# --- the rows the command builds ---------------------------------------------


def test_should_list_installed_profiles_before_ones_only_the_config_knows(tmp_path: Path):
    state = _state(
        tmp_path,
        project={'agents': {'wizard': {'account': 'work', 'model': 'm', 'effort': 'high'}}},
        user={'agents': {'gone': {'account': 'deleted', 'model': 'm', 'effort': 'low'}}},
    )

    rows = defaults_rows(
        state,
        profiles={'wizard': _profile('wizard', AgentRole.AGENT)},
        active='wizard',
        providers={'work': 'OpenRouter'},
    )

    assert [row.target for row in rows] == [None, 'wizard', 'gone']
    assert rows[1].label == 'Wizard' and rows[1].active is True
    # The alias is a name the user chose: the provider is what it talks to.
    assert rows[1].summary == 'work · OpenRouter · m · high'
    assert rows[2].installed is False
    assert "account 'deleted' is not configured" in rows[2].summary


def test_should_list_agents_and_subagents_each_with_its_own_role(tmp_path: Path):
    state = _state(tmp_path)
    profiles = {
        'wizard': _profile('wizard', AgentRole.AGENT),
        'reviewer': _profile('reviewer', AgentRole.AGENT),
        'scout': _profile('scout', AgentRole.SUBAGENT),
        'pas_qa': _profile('pas_qa', AgentRole.SUBAGENT),
    }

    rows = defaults_rows(state, profiles=profiles, active='wizard')

    assert [row.target for row in rows] == [None, 'reviewer', 'wizard', 'pas_qa', 'scout']
    assert [row.role for row in rows[1:3]] == [AgentRole.AGENT, AgentRole.AGENT]
    assert [row.role for row in rows[3:]] == [AgentRole.SUBAGENT, AgentRole.SUBAGENT]
    assert [row.label for row in rows[3:]] == ['Pas Qa', 'Scout']


def test_should_head_each_role_and_the_orphans_apart(tmp_path: Path):
    state = _state(
        tmp_path,
        project={'agents': {'gone': {'account': 'work', 'model': 'm', 'effort': 'low'}}},
    )
    rows = defaults_rows(
        state,
        profiles={
            'wizard': _profile('wizard', AgentRole.AGENT),
            'scout': _profile('scout', AgentRole.SUBAGENT),
        },
    )

    text = _mounted(ModelDefaultsScreen(rows, state))['list']

    assert 'Agents\n' in text
    assert 'Subagents\n' in text
    assert 'In the config, not installed\n' in text
    # The heading is on the row that opens the section, not repeated on the next one.
    assert text.count('Agents') == 1


def test_should_say_where_each_kind_of_default_takes_effect():
    from src.ui.provider_mixin import _effect_note

    default = DefaultsRow(None, 'Default for new conversations', '')
    agent = DefaultsRow('wizard', 'Wizard', '', role=AgentRole.AGENT)
    subagent = DefaultsRow('scout', 'Scout', '', role=AgentRole.SUBAGENT)

    assert 'New conversations start on it' in _effect_note(default)
    assert 'New conversations and that agent' in _effect_note(agent)
    # A subagent never starts a conversation: it answers a summon.
    assert 'Every summon of that subagent' in _effect_note(subagent)


# --- the whole flow, in the real app ----------------------------------------


def _sigil_app(root: Path, service, paths: tuple[Path, Path]):
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from src.engine.sessions.session_store import SessionStore
    from src.ui.app import SigilApp

    store = SessionStore(cwd=str(root), base_dir=root / 'sessions')
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=store.create_session(),
        model_service=service,
        model_defaults_state=lambda: read_defaults(paths=paths),
        save_model_default=lambda target, config, scope: save_default(target, config, scope, paths=paths),
    )


def test_should_write_the_default_model_chosen_from_the_chat(tmp_path: Path):
    from src.ui.widgets.effort_screen import EffortScreen
    from src.ui.widgets.grouped_model_screen import GroupedModelScreen
    from tests.test_provider_commands import _prepared, _submit, _texts, wait_until

    async def inner() -> None:
        project, user = _files(tmp_path)
        app = _sigil_app(tmp_path, await _prepared(tmp_path), (project, user))
        async with app.run_test() as pilot:
            _submit(app, '/default-models')
            await wait_until(pilot, lambda: isinstance(app.screen, ModelDefaultsScreen))
            # The default row is set nowhere, so the destination starts on the user's
            # file; Tab moves the write to the project.
            await pilot.press('tab', 'enter')
            await wait_until(pilot, lambda: isinstance(app.screen, GroupedModelScreen))
            await pilot.press('enter')
            await wait_until(pilot, lambda: isinstance(app.screen, EffortScreen))
            await pilot.press('enter')
            await wait_until(pilot, lambda: isinstance(app.screen, ModelDefaultsScreen))
            await pilot.press('escape')
            await wait_until(pilot, lambda: not isinstance(app.screen, ModelDefaultsScreen))
            notices = _texts(app)

        written = json.loads(project.read_text(encoding='utf-8'))
        assert written['default_model'] == {'account': 'work', 'model': 'first/model', 'effort': 'default'}
        assert not user.exists()
        assert any('project config' in text for text in notices)

    asyncio.run(inner())


def test_should_say_when_the_other_file_still_overrides_the_write(tmp_path: Path):
    """Writing the user's file while the project sets the same agent is not a lie."""
    from src.ui.widgets.effort_screen import EffortScreen
    from src.ui.widgets.grouped_model_screen import GroupedModelScreen
    from tests.test_provider_commands import _prepared, _submit, _texts, wait_until

    async def inner() -> None:
        project, user = _files(tmp_path)
        project.write_text(
            json.dumps({'agents': {'wizard': {'account': 'work', 'model': 'second/model', 'effort': 'low'}}}),
            encoding='utf-8',
        )
        app = _sigil_app(tmp_path, await _prepared(tmp_path), (project, user))
        async with app.run_test() as pilot:
            _submit(app, '/default-models')
            await wait_until(pilot, lambda: isinstance(app.screen, ModelDefaultsScreen))
            # The row is set by the project, so the destination starts there; Tab
            # moves the write to the user's file, which the project then shadows.
            await pilot.press('down', 'tab', 'enter')
            await wait_until(pilot, lambda: isinstance(app.screen, GroupedModelScreen))
            await pilot.press('enter')
            await wait_until(pilot, lambda: isinstance(app.screen, EffortScreen))
            await pilot.press('enter')
            await wait_until(pilot, lambda: isinstance(app.screen, ModelDefaultsScreen))
            notices = _texts(app)
            await pilot.press('escape')
            await wait_until(pilot, lambda: not isinstance(app.screen, ModelDefaultsScreen))

        assert json.loads(user.read_text(encoding='utf-8'))['agents']['wizard']['model'] == 'first/model'
        assert any('still overrides' in text for text in notices)

    asyncio.run(inner())
