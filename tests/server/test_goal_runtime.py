"""Goal evaluation follows the real session consumer and durable turns."""

import asyncio
from pathlib import Path

from pydantic_ai.models.test import TestModel

from src.core.composition.composition import Composition
from src.core.config.load_settings import load_settings
from src.core.goal.review import GoalDecision
from src.core.goal.state import Goal
from src.core.providers.provider_paths import ProviderPaths
from src.server.runtime.event_hub import EventHub
from src.server.runtime.server_runtime import ServerRuntime


def test_goal_continues_then_completes_without_intermediate_finish(tmp_path: Path, monkeypatch) -> None:
    from src.server.runtime import session_runtime as module

    async def run() -> None:
        home = tmp_path / 'home'
        home.mkdir()
        composition = Composition(
            tmp_path,
            settings=load_settings(),
            model=TestModel(custom_output_text='work report', call_tools=[]),
            provider_paths=ProviderPaths(home),
        )
        composition.meter.set_window(100_000)
        runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
        runtime.session.goal = Goal('ship export')
        decisions = iter(
            [
                GoalDecision(decision='continue', reason='Run the missing tests.'),
                GoalDecision(decision='complete', reason='The work is complete.'),
            ]
        )
        seen = []

        async def review(model, snapshot, **kwargs):
            seen.append(snapshot)
            return next(decisions)

        monkeypatch.setattr(module, 'review_goal', review)
        try:
            await runtime.send('Start work')
            await asyncio.wait_for(runtime.wait(), 10)
            assert len(seen) == 2
            assert runtime.session.goal.status == 'completed'
            events = runtime.hub.replay()
            assert sum(event.type == 'turn_finished' for event in events) == 1
            assert len(runtime.session.messages) >= 4
            from pydantic_ai.messages import ModelRequest, UserPromptPart

            from src.core.agents.type_of import type_of

            origins = [
                type_of(message, part).get('type')
                for message in runtime.session.messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart) and 'Run the missing tests' in str(part.content)
            ]
            assert origins == ['agent_message']
        finally:
            runtime.close()

    asyncio.run(run())


def test_abort_cancels_pending_review_without_starting_another_turn(tmp_path: Path, monkeypatch) -> None:
    from src.server.runtime import session_runtime as module

    async def run() -> None:
        home = tmp_path / 'home'
        home.mkdir()
        composition = Composition(
            tmp_path,
            settings=load_settings(),
            model=TestModel(custom_output_text='work report', call_tools=[]),
            provider_paths=ProviderPaths(home),
        )
        composition.meter.set_window(100_000)
        runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
        runtime.session.goal = Goal('ship export')
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def review(model, snapshot, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(module, 'review_goal', review)
        try:
            await runtime.send('Start work')
            await asyncio.wait_for(started.wait(), 10)
            assert runtime.abort()
            await asyncio.wait_for(cancelled.wait(), 10)
            await asyncio.wait_for(runtime.wait(), 10)
            assert runtime.session.goal.status == 'stopped'
            assert sum(event.type == 'turn_started' for event in runtime.hub.replay()) == 1
        finally:
            runtime.close()

    asyncio.run(run())


def test_review_waits_for_running_subagent(tmp_path: Path, monkeypatch) -> None:
    from src.server.runtime import session_runtime as module

    async def run() -> None:
        home = tmp_path / 'home'
        home.mkdir()
        composition = Composition(
            tmp_path,
            settings=load_settings(),
            model=TestModel(custom_output_text='work report', call_tools=[]),
            provider_paths=ProviderPaths(home),
        )
        composition.meter.set_window(100_000)
        runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
        runtime.session.goal = Goal('ship export')
        running = True
        seen = []
        owner = runtime.session_runtime
        monkeypatch.setattr(owner, '_subagent_working', lambda: running)

        async def review(model, snapshot, **kwargs):
            seen.append(snapshot)
            return GoalDecision(decision='complete', reason='Complete')

        monkeypatch.setattr(module, 'review_goal', review)
        try:
            await runtime.send('Start work')
            await asyncio.wait_for(runtime.wait(), 10)
            assert not seen
            assert runtime.session.goal.status == 'active'
            running = False
            owner._on_tree_change(object())
            await asyncio.wait_for(runtime.wait(), 10)
            assert len(seen) == 1
            assert runtime.session.goal.status == 'completed'
        finally:
            runtime.close()

    asyncio.run(run())


def test_new_user_input_invalidates_an_inflight_review(tmp_path: Path, monkeypatch) -> None:
    from src.server.runtime import session_runtime as module

    async def run() -> None:
        home = tmp_path / 'home'
        home.mkdir()
        composition = Composition(
            tmp_path,
            settings=load_settings(),
            model=TestModel(custom_output_text='work report', call_tools=[]),
            provider_paths=ProviderPaths(home),
        )
        composition.meter.set_window(100_000)
        runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
        runtime.session.goal = Goal('ship export')
        review_started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def review(model, snapshot, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                review_started.set()
                await release.wait()
                return GoalDecision(decision='continue', reason='Stale instruction')
            return GoalDecision(decision='complete', reason='Done')

        monkeypatch.setattr(module, 'review_goal', review)
        try:
            await runtime.send('Start work')
            await asyncio.wait_for(review_started.wait(), 10)
            assert runtime.busy
            accepted = await runtime.send('New instruction')
            assert accepted['accepted'] == 'turn'
            release.set()
            await asyncio.wait_for(runtime.wait(), 10)
            assert calls == 2
            assert runtime.session.goal.status == 'completed'
            assert sum(event.type == 'turn_started' for event in runtime.hub.replay()) == 2
            assert 'Stale instruction' not in str(runtime.session.messages)
        finally:
            runtime.close()

    asyncio.run(run())


def test_review_limit_pauses_without_another_model_call(tmp_path: Path, monkeypatch) -> None:
    from src.server.runtime import session_runtime as module

    async def run() -> None:
        home = tmp_path / 'home'
        home.mkdir()
        composition = Composition(
            tmp_path,
            settings=load_settings({'SIGIL_GOAL_MAX_CONTINUATIONS': '2'}),
            model=TestModel(custom_output_text='work report', call_tools=[]),
            provider_paths=ProviderPaths(home),
        )
        composition.meter.set_window(100_000)
        runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
        runtime.session.goal = Goal('ship export', continuations=2)

        async def unexpected(*args, **kwargs):
            raise AssertionError('review must not be called after the limit')

        monkeypatch.setattr(module, 'review_goal', unexpected)
        try:
            await runtime.send('Start work')
            await asyncio.wait_for(runtime.wait(), 10)
            assert runtime.session.goal.status == 'blocked'
            assert any('continuation limit' in str(event.data) for event in runtime.hub.replay())
        finally:
            runtime.close()

    asyncio.run(run())


def test_review_error_pauses_goal_without_claiming_success(tmp_path: Path, monkeypatch) -> None:
    from src.server.runtime import session_runtime as module

    async def run() -> None:
        home = tmp_path / 'home'
        home.mkdir()
        composition = Composition(
            tmp_path,
            settings=load_settings(),
            model=TestModel(custom_output_text='work report', call_tools=[]),
            provider_paths=ProviderPaths(home),
        )
        composition.meter.set_window(100_000)
        runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
        runtime.session.goal = Goal('ship export')

        async def broken(*args, **kwargs):
            raise RuntimeError('review unavailable')

        monkeypatch.setattr(module, 'review_goal', broken)
        try:
            await runtime.send('Start work')
            await asyncio.wait_for(runtime.wait(), 10)
            assert runtime.session.goal.status == 'blocked'
            assert any('review unavailable' in str(event.data) for event in runtime.hub.replay())
        finally:
            runtime.close()

    asyncio.run(run())


def test_new_user_message_resumes_a_blocked_goal(tmp_path: Path, monkeypatch) -> None:
    from src.server.runtime import session_runtime as module

    async def run() -> None:
        home = tmp_path / 'home'
        home.mkdir()
        composition = Composition(
            tmp_path,
            settings=load_settings(),
            model=TestModel(custom_output_text='work report', call_tools=[]),
            provider_paths=ProviderPaths(home),
        )
        composition.meter.set_window(100_000)
        runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
        runtime.session.goal = Goal('ship export', status='blocked', continuations=10)

        async def review(*args, **kwargs):
            return GoalDecision(decision='complete', reason='Done')

        monkeypatch.setattr(module, 'review_goal', review)
        try:
            await runtime.send('Here is the missing information')
            await asyncio.wait_for(runtime.wait(), 10)
            assert runtime.session.goal.status == 'completed'
            assert runtime.session.goal.continuations == 0
        finally:
            runtime.close()

    asyncio.run(run())


def test_goal_change_during_review_discards_stale_decision(tmp_path: Path, monkeypatch) -> None:
    from src.server.runtime import session_runtime as module

    async def run() -> None:
        home = tmp_path / 'home'
        home.mkdir()
        composition = Composition(
            tmp_path,
            settings=load_settings(),
            model=TestModel(custom_output_text='work report', call_tools=[]),
            provider_paths=ProviderPaths(home),
        )
        composition.meter.set_window(100_000)
        runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
        runtime.session.goal = Goal('old')
        started = asyncio.Event()
        release = asyncio.Event()
        goals = []

        async def review(model, snapshot, **kwargs):
            goals.append(snapshot.goal.text)
            if len(goals) == 1:
                started.set()
                await release.wait()
            return GoalDecision(decision='complete', reason='Done')

        monkeypatch.setattr(module, 'review_goal', review)
        try:
            await runtime.send('Start')
            await asyncio.wait_for(started.wait(), 10)
            runtime.session.goal = runtime.session.goal.replace('new')
            release.set()
            await asyncio.wait_for(runtime.wait(), 10)
            assert goals == ['old', 'new']
            assert runtime.session.goal.text == 'new'
            assert runtime.session.goal.status == 'completed'
        finally:
            runtime.close()

    asyncio.run(run())


def test_identical_review_evidence_pauses_instead_of_looping(tmp_path: Path, monkeypatch) -> None:
    from src.server.runtime import session_runtime as module

    async def run() -> None:
        home = tmp_path / 'home'
        home.mkdir()
        composition = Composition(
            tmp_path,
            settings=load_settings(),
            model=TestModel(custom_output_text='same report', call_tools=[]),
            provider_paths=ProviderPaths(home),
        )
        composition.meter.set_window(100_000)
        runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
        runtime.session.goal = Goal('ship export')
        calls = 0

        async def review(*args, **kwargs):
            nonlocal calls
            calls += 1
            return GoalDecision(decision='continue', reason='Run tests')

        monkeypatch.setattr(module, 'review_goal', review)
        try:
            await runtime.send('Start work')
            await asyncio.wait_for(runtime.wait(), 10)
            assert calls == 3
            assert runtime.session.goal.status == 'blocked'
            assert runtime.session.goal.continuations == 2
        finally:
            runtime.close()

    asyncio.run(run())


def test_abort_stops_goal_while_waiting_for_subagent(tmp_path: Path, monkeypatch) -> None:
    async def run() -> None:
        home = tmp_path / 'home'
        home.mkdir()
        composition = Composition(
            tmp_path,
            settings=load_settings(),
            model=TestModel(custom_output_text='work report', call_tools=[]),
            provider_paths=ProviderPaths(home),
        )
        composition.meter.set_window(100_000)
        runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
        runtime.session.goal = Goal('ship export')
        monkeypatch.setattr(runtime.session_runtime, '_subagent_working', lambda: True)
        try:
            await runtime.send('Start')
            await asyncio.wait_for(runtime.wait(), 10)
            assert runtime.abort()
            assert runtime.session.goal.status == 'stopped'
        finally:
            runtime.close()

    asyncio.run(run())


def test_goal_update_steers_active_run_without_forging_user_input(tmp_path: Path, monkeypatch) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    from pydantic_ai.models.function import FunctionModel

    from src.core.agents.type_of import type_of
    from src.core.goal.state import parse_goal_command
    from src.server.runtime import session_runtime as module

    async def run() -> None:
        home = tmp_path / 'home'
        home.mkdir()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def respond(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
            yield 'Done'

        composition = Composition(
            tmp_path,
            settings=load_settings(),
            model=TestModel(call_tools=[]),
            provider_paths=ProviderPaths(home),
        )
        runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
        composition.agent = Agent(FunctionModel(stream_function=respond))

        async def review(model, snapshot, **kwargs):
            return GoalDecision(decision='complete', reason='Done')

        monkeypatch.setattr(module, 'review_goal', review)
        try:
            await runtime.send('Start work')
            await asyncio.wait_for(started.wait(), 5)
            runtime.session_runtime.apply_goal(parse_goal_command('--yolo Ship CSV'))
            release.set()
            await asyncio.wait_for(runtime.wait(), 10)
            updates = [
                type_of(message, part)
                for message in runtime.session.messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart) and 'Ship CSV' in str(part.content)
            ]
            assert updates == [{'type': 'internal_notice'}]
        finally:
            release.set()
            runtime.close()

    asyncio.run(run())
