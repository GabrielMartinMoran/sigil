"""Reproducible, offline core benchmarks: uv run python -m scripts.benchmark_core.

All fixtures live in a temporary directory. No production configuration, credentials,
models, agents, or external MCP servers are loaded. Timings are diagnostic, not CI gates.
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import hashlib
import inspect
import io
import json
import os
import platform
import pstats
import random
import selectors
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml
from PIL import Image
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    BinaryImage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.profile.registry import AgentRegistry
from src.engine.agents.tree.models.node_status import NodeStatus
from src.engine.agents.tree.store import AgentTreeStore
from src.engine.agents.tree.summoner import SubagentSummoner
from src.engine.agents.tree.tools import register_tree_tools, render_tree_table
from src.engine.agents.tree.tree import AgentTree
from src.engine.attachments.images import prepare_image
from src.engine.attachments.tools import image_id_for, register_image_tools
from src.engine.context.budget import ContextBudget
from src.engine.context.capability import ContextViewCapability
from src.engine.context.compaction.events import RECORD_KEY
from src.engine.context.input_estimate import history_fingerprint
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.strategy.append_only import AppendOnlyStrategy
from src.engine.files.models.file_content import FileContent
from src.engine.files.models.file_tool_error import FileToolError
from src.engine.files.text import apply_string_patch, decode
from src.engine.files.tools import register_file_tools
from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.interaction.tools import register_interaction_tools
from src.engine.mcp.config_models.server_config import MCPServerConfig
from src.engine.mcp.config_models.server_spec import MCPServerSpec
from src.engine.mcp.manager.manager import MCPManager
from src.engine.mcp.tools import register_mcp_tools
from src.engine.roadmap.store import RoadmapStore
from src.engine.roadmap.tools import RoadmapItemInput, register_roadmap_tools
from src.engine.sessions.session_store import SessionStore
from src.engine.shell.process import DrainResult, _Buffers, _read_available, kill_process_group
from src.engine.shell.runner import execute_command
from src.engine.shell.tools import register_shell_tools
from src.engine.skills.registry import SkillRegistry
from src.engine.skills.rendering import render_loaded_skills
from src.engine.skills.tools import register_skill_tools
from src.engine.storage.database import Database
from src.engine.tooling.events import preview
from src.engine.tooling.logging import ToolLoggerHub
from src.engine.tooling.outputs import present_output
from src.engine.tooling.wrapper import wrap_tool
from src.engine.web.tools import register_web_tools


class Collector:
    """Capture registered wrappers without paying model/schema construction costs."""

    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}

    def tool_plain(self, func: Callable[..., Any], **options: Any) -> None:
        self.tools[options.get('name', func.__name__)] = func

    def tool(self, func: Callable[..., Any], **options: Any) -> None:
        self.tool_plain(func, **options)


def history(count: int) -> list[Any]:
    messages: list[Any] = []
    for index in range(count):
        run_id = f'run-{index // 10}'
        call_id = f'call-{index}'
        messages.extend(
            [
                ModelResponse([ToolCallPart('read_file', {'path': 'sample.py'}, tool_call_id=call_id)], run_id=run_id),
                ModelRequest(
                    [ToolReturnPart('read_file', {'content': 'print(1)\n' * 256}, tool_call_id=call_id)], run_id=run_id
                ),
            ]
        )
    return [ModelRequest([UserPromptPart('Inspect the project')], run_id='run-0'), *messages]


def measure(label: str, call: Callable[[], Any], repeats: int, rounds: int) -> dict[str, Any]:
    call()
    samples = []
    for _ in range(rounds):
        start = time.perf_counter_ns()
        for _ in range(repeats):
            call()
        samples.append((time.perf_counter_ns() - start) / repeats / 1e6)
    return record(label, samples, repeats)


async def measure_async(label: str, call: Callable[[], Any], repeats: int, rounds: int) -> dict[str, Any]:
    await call()
    samples = []
    for _ in range(rounds):
        start = time.perf_counter_ns()
        for _ in range(repeats):
            await call()
        samples.append((time.perf_counter_ns() - start) / repeats / 1e6)
    return record(label, samples, repeats)


def record(label: str, samples: list[float], repeats: int) -> dict[str, Any]:
    print(f'{label}: {statistics.median(samples):.3f} ms', flush=True)
    return {
        'label': label,
        'median_ms': statistics.median(samples),
        'min_ms': min(samples),
        'max_ms': max(samples),
        'round_means_ms': samples,
        'calls_per_round': repeats,
    }


def profile(call: Callable[[], Any]) -> str:
    profiler = cProfile.Profile()
    profiler.runcall(call)
    output = io.StringIO()
    pstats.Stats(profiler, stream=output).strip_dirs().sort_stats('cumulative').print_stats(16)
    return output.getvalue()


def bounded_read(path: Path, limit: int) -> bytes:
    with path.open('rb') as handle:
        return handle.read(limit)


def range_candidate(path: Path, first: int, last: int) -> tuple[str, int]:
    """Keep total_lines semantics without allocating one string per file line."""
    raw = path.read_bytes()
    text = raw.decode('utf-8')
    total = text.count('\n') + bool(text and not text.endswith('\n'))
    start = 0
    for _ in range(first - 1):
        start = text.find('\n', start) + 1
    end = start
    for _ in range(last - first + 1):
        found = text.find('\n', end)
        end = len(text) if found < 0 else found + 1
    return text[start:end], total


def drain_candidate(process: subprocess.Popen[bytes], deadline: float) -> DrainResult:
    """Keep bounded draining; wait for exit instead of polling an empty selector."""
    buffers = _Buffers()
    timed_out = False
    try:
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdout, process.stderr):
                assert stream is not None
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, stream is process.stdout)
            while selector.get_map():
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                _read_available(selector, buffers)
            if not timed_out:
                try:
                    process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
            if timed_out:
                kill_process_group(process)
                process.wait(timeout=2)
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    return DrainResult(
        stdout=bytes(buffers.stdout),
        stderr=bytes(buffers.stderr),
        stdout_truncated=buffers.stdout_truncated,
        stderr_truncated=buffers.stderr_truncated,
        timed_out=timed_out,
        returncode=process.returncode,
    )


def whole_candidate(target: Path, size: int, max_bytes: int) -> FileContent:
    raw = bounded_read(target, max_bytes + 1)
    content, binary, encoding = decode(raw[:max_bytes])
    return FileContent(
        path=str(target), size=size, truncated=len(raw) > max_bytes, binary=binary, encoding=encoding, content=content
    )


def communicate(command: str) -> subprocess.CompletedProcess[bytes]:
    """Comparison only: lacks the production output cap and descendant cancellation."""
    return subprocess.run(
        ['/bin/bash', '--noprofile', '--norc', '-c', command],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        start_new_session=True,
        timeout=5,
        check=True,
    )


def memoized_view(strategy: Any, messages: list[Any], budget: ContextBudget) -> Any:
    """Prototype: reuse estimates only within one synchronous projection invocation."""
    from src.engine.context.compaction import measurements as projection

    estimate = projection.estimate_input
    tokens = projection.estimate_messages_tokens
    estimates: dict[Any, Any] = {}
    counts: dict[Any, Any] = {}

    def cached_estimate(sequence: Any, budget: ContextBudget) -> Any:
        key = (tuple(map(id, sequence)), budget)
        if key not in estimates:
            estimates[key] = estimate(sequence, budget)
        return estimates[key]

    def cached_tokens(sequence: Any) -> Any:
        key = tuple(map(id, sequence))
        if key not in counts:
            counts[key] = tokens(sequence)
        return counts[key]

    with (
        patch.object(projection, 'estimate_input', cached_estimate),
        patch.object(projection, 'estimate_messages_tokens', cached_tokens),
    ):
        return strategy.view(messages, budget)


class InstantRun:
    """Synthetic steering receiver: no agent task or LLM is started."""

    def enqueue(self, text: str) -> None:
        self.last_message = text


async def tree_controls(rounds: int) -> list[dict[str, Any]]:
    trees = AgentTreeStore()
    tree = trees.active
    tree.ensure_root('benchmark')
    child = tree.add(parent_id=tree.root_id, name='synthetic', role=AgentRole.SUBAGENT)
    child.run = InstantRun()
    summoner = SubagentSummoner(trees=trees, registry=AgentRegistry(roots=[]), builder=lambda: None)
    tools = Collector()
    register_tree_tools(tools, summoner, logger=lambda event: None)
    context = RunContext(deps=None, model=TestModel(), usage=RunUsage(), messages=[])
    notice = tree.notifications.publish(child, tree.root_id, 'Benchmark notice')
    results = [
        await measure_async(
            'send_message/synthetic_steering_no_task',
            lambda: tools.tools['send_message'](context, agent_id=child.id, message='Continue'),
            30,
            rounds,
        )
    ]
    results.append(
        await measure_async(
            'ack_notifications/idempotent_no_persistence',
            lambda: tools.tools['ack_notifications'](context, notification_ids=[notice.id]),
            30,
            rounds,
        )
    )

    def stop() -> str:
        child.status = NodeStatus.RUNNING
        child.finished_at = None
        return tools.tools['kill_subagent'](context, agent_id=child.id)

    results.append(measure('kill_subagent/synthetic_state_no_task', stop, 30, rounds))
    results.append(measure('list_agents/wrapped_2nodes', lambda: tools.tools['list_agents'](context), 30, rounds))
    return results


async def main(root: Path, rounds: int) -> dict[str, Any]:
    results = []
    profiles = {}
    logger = lambda event: None
    tools = Collector()
    small = root / 'small.py'
    small.write_text('print(1)\n' * 1024)
    large = root / 'large.py'
    large.write_bytes(b'print(1)\n' * (32 * 1024 * 1024 // 9))
    output = root / 'output.py'
    output.write_text('before\n' * 1024)
    register_file_tools(tools, logger, root=lambda: root)
    register_shell_tools(tools, logger, root=lambda: root)
    register_image_tools(tools, logger, root=lambda: root)
    skill_root = root / 'skills'
    for index in range(30):
        skill = skill_root / f'skill-{index}'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text(
            f'---\nname: skill-{index}\ndescription: A reproducible benchmark skill\n---\n' + 'Instructions.\n' * 100
        )
        for resource in range(20):
            (skill / f'resource-{resource}.txt').write_text('Reference material\n' * 100)
        (skill / 'script.sh').write_text('printf benchmark')
    registry = SkillRegistry([('custom', skill_root)])
    register_skill_tools(tools, registry, logger)
    store = SessionStore(cwd=root, base_dir=root / 'state')
    session = store.create_session()
    roadmap = RoadmapStore(session.root_id, base_dir=root / 'state')
    register_roadmap_tools(tools, roadmap, logger)
    hub = QuestionHub()

    async def answer(questions: list[QuestionItem]) -> list[QuestionAnswer]:
        return [QuestionAnswer(question=questions[0].question, answer='Yes')]

    hub.set_asker(answer)
    register_interaction_tools(tools, hub, logger)
    register_web_tools(tools, logger)
    context = RunContext(deps=None, model=TestModel(), usage=RunUsage(), messages=[])
    questions = [QuestionItem(question='Continue?', options=[{'label': 'Yes'}, {'label': 'No'}])]
    cases = [
        ('read_file/9KiB', 'read_file', {'path': str(small)}, None),
        ('read_file/32MiB_cap4KiB', 'read_file', {'path': str(large), 'max_bytes': 4096}, None),
        ('read_file/32MiB_lines1-40', 'read_file', {'path': str(large), 'start_line': 1, 'end_line': 40}, None),
        ('write_file/7KiB', 'write_file', {'path': str(output), 'content': 'before\n' * 1024}, None),
        (
            'patch_file/string7KiB',
            'patch_file',
            {'path': str(output), 'old_string': 'before', 'new_string': 'before', 'replace_all': True},
            None,
        ),
        (
            'patch_file/lines7KiB',
            'patch_file',
            {'path': str(output), 'start_line': 1, 'end_line': 2, 'new_string': 'before\nbefore\n'},
            None,
        ),
        ('list_skills/30', 'list_skills', {}, None),
        ('load_skill/21resources', 'load_skill', {'name': 'skill-0'}, None),
        ('read_skill_resource/1.8KiB', 'read_skill_resource', {'name': 'skill-0', 'path': 'resource-0.txt'}, None),
        (
            'update_roadmap/10items_SQLite_FULL',
            'update_roadmap',
            {'action': 'set', 'items': [RoadmapItemInput(title=f'Task {i}') for i in range(10)]},
            context,
        ),
        ('ask_user/instant_answer_noUI', 'ask_user', {'questions': questions}, context),
        ('run/printf', 'run', {'cmds': ['printf benchmark']}, None),
        ('run/8printf', 'run', {'cmds': ['printf benchmark'] * 8}, None),
        ('run/output1MiB', 'run', {'cmds': ['head -c 1048576 /dev/zero']}, None),
        ('run_skill_script/bash', 'run_skill_script', {'name': 'skill-0', 'script': 'script.sh'}, None),
    ]
    # Do not invoke login-shell startup files or collect user exports in this offline harness.
    with patch('src.engine.shell.environment._cache', {}):
        for label, name, kwargs, ctx in cases:
            func = tools.tools[name]
            args = () if ctx is None else (ctx,)
            call = lambda func=func, args=args, kwargs=kwargs: func(*args, **kwargs)
            repeats = 3 if '32MiB' in label else 15
            result = (
                await measure_async(label, call, repeats, rounds)
                if inspect.iscoroutinefunction(func)
                else measure(label, call, repeats, rounds)
            )
            results.append(result)
    with patch('src.engine.shell.environment._cache', {}):
        for label, call in [
            ('shell/raw_printf', lambda: execute_command('printf benchmark', cwd=root)),
            ('shell/raw_output1MiB', lambda: execute_command('head -c 1048576 /dev/zero', cwd=root)),
            ('candidate/shell_communicate_printf', lambda: communicate('printf benchmark')),
            ('candidate/shell_communicate_1MiB', lambda: communicate('head -c 1048576 /dev/zero')),
        ]:
            results.append(measure(label, call, 15, rounds))
        profiles['shell_printf'] = profile(lambda: execute_command('printf benchmark', cwd=root))
        with patch('src.engine.shell.runner.drain', drain_candidate):
            for command, label in [('printf benchmark', 'printf'), ('head -c 1048576 /dev/zero', 'output1MiB')]:
                results.append(
                    measure(
                        f'candidate/shell_bounded_wait_{label}',
                        lambda command=command: execute_command(command, cwd=root),
                        15,
                        rounds,
                    )
                )
            capped = execute_command('head -c 1048576 /dev/zero', cwd=root)
            assert len(capped.stdout) == 65536 and capped.stdout_truncated and capped.returncode == 0
            timed = execute_command('exec 1>&- 2>&-; sleep 5', timeout_seconds=1, cwd=root)
            assert timed.timed_out and timed.returncode == 124

    for sink_name, sink in [('sync', logger), ('hub', ToolLoggerHub([logger]))]:
        wrapped = wrap_tool(lambda: 1, tool_name='noop', logger=sink)
        results.append(
            await measure_async(
                f'wrapper/thread_noop_{sink_name}', lambda wrapped=wrapped: asyncio.to_thread(wrapped), 30, rounds
            )
        )
    results.append(measure('candidate/read_cap4KiB_IOonly', lambda: bounded_read(large, 4096), 100, rounds))
    baseline = tools.tools['read_file'](str(large), max_bytes=4096)
    with patch('src.engine.files.tools._read_whole', whole_candidate):
        assert tools.tools['read_file'](str(large), max_bytes=4096) == baseline
        results.append(
            measure(
                'candidate/read_cap4KiB_FULLtool',
                lambda: tools.tools['read_file'](str(large), max_bytes=4096),
                30,
                rounds,
            )
        )

    results.append(measure('candidate/range32MiB_count+find', lambda: range_candidate(large, 1, 40), 3, rounds))
    actual = tools.tools['read_file'](str(large), start_line=1, end_line=40)
    candidate, total = range_candidate(large, 1, 40)
    assert (candidate, total) == (actual.content, actual.total_lines)
    assert bounded_read(large, 4096).decode() == tools.tools['read_file'](str(large), max_bytes=4096).content

    raw_result = tools.tools['read_file'](str(small))
    for label, call in [
        ('wrapper/read9KiB_raw', lambda: tools.tools['read_file'].__wrapped__(str(small))),
        ('wrapper/read9KiB_wrapped', lambda: tools.tools['read_file'](str(small))),
        ('output/preview9KiB', lambda: preview(raw_result)),
        ('output/present9KiB', lambda: present_output('read_file', raw_result)),
        ('skills/render10_loaded', lambda: render_loaded_skills(registry, [f'skill-{i}' for i in range(10)])),
        (
            'yaml/SafeLoader',
            lambda: yaml.safe_load('name: sample\ndescription: A benchmark\nmetadata:\n  version: 1\n'),
        ),
        (
            'yaml/CSafeLoader',
            lambda: yaml.load(
                'name: sample\ndescription: A benchmark\nmetadata:\n  version: 1\n', Loader=yaml.CSafeLoader
            ),
        ),
    ]:
        results.append(measure(label, call, 30, rounds))

    text = 'a\n' * 50000
    results.append(measure('patch/ambiguous_50k_matches', lambda: ambiguous_patch(text), 1, rounds))
    results.append(
        measure('patch/replace_all_50k_matches', lambda: apply_string_patch(text, 'a', 'b', True), 3, rounds)
    )
    results.append(
        measure('candidate/replace_count_native', lambda: (text.replace('a', 'b'), text.count('a')), 20, rounds)
    )

    image = Image.frombytes('L', (1920, 1080), random.Random(0).randbytes(1920 * 1080)).convert('RGB')
    image_path = root / 'image.png'
    image.save(image_path)
    image_bytes = image_path.read_bytes()
    prepared = prepare_image(image_bytes)
    identifier = image_id_for(str(image_path.resolve()))
    image_context = replace(
        context,
        messages=[
            ModelRequest(
                [UserPromptPart([BinaryImage(data=prepared.data, media_type='image/png', identifier=identifier)])],
                run_id='image-turn',
            )
        ],
    )
    results.append(
        await measure_async(
            'view_image/1920x1080_new', lambda: tools.tools['view_image'](context, source=str(image_path)), 2, rounds
        )
    )
    results.append(
        await measure_async(
            'view_image/1920x1080_duplicate',
            lambda: tools.tools['view_image'](image_context, source=str(image_path)),
            2,
            rounds,
        )
    )
    for optimize in (True, False):

        def encode(optimize: bool = optimize) -> bytes:
            buffer = io.BytesIO()
            image.save(buffer, format='PNG', optimize=optimize)
            return buffer.getvalue()

        results.append({**measure(f'png/encode_optimize{optimize}', encode, 2, rounds), 'bytes': len(encode())})

    # Model requests are deterministic, immediate, and never sent over a network.
    def model(messages: list[Any], info: Any) -> ModelResponse:
        if isinstance(messages[-1].parts[0], ToolReturnPart):
            return ModelResponse([TextPart('done')])
        return ModelResponse([ToolCallPart('read_file', {'path': str(small)}, tool_call_id='read')])

    for count in (10, 100, 500):
        messages = history(count)
        # Match the runtime path that verifies a provider-measured prefix.
        response_index = len(messages) - 2
        messages[response_index].metadata = {
            RECORD_KEY: {
                'version': 4,
                'state': {},
                'events': [],
                'input_fingerprint': history_fingerprint(messages[:response_index]),
                'fixed_tokens': 0,
            },
        }
        messages[response_index].model_name = 'benchmark'
        messages[response_index].usage.input_tokens = count * 600
        strategy = AppendOnlyStrategy()
        budget = ContextBudget(window=10_000_000, model_name='benchmark')
        call = lambda strategy=strategy, messages=messages, budget=budget: strategy.view(messages, budget)
        results.append(measure(f'context/view_{len(messages)}messages', call, 2, rounds))
        if count == 500:
            profiles['context_1001messages'] = profile(call)
        results.append(
            measure(
                f'context/fingerprint_{len(messages)}messages',
                lambda messages=messages: history_fingerprint(messages),
                3,
                rounds,
            )
        )
        if count == 500:
            expected = call()
            candidate_call = lambda strategy=strategy, messages=messages, budget=budget: memoized_view(
                strategy, messages, budget
            )
            assert candidate_call() == expected
            results.append(measure('candidate/context_request_local_memo_1001', candidate_call, 2, rounds))
        session.messages = messages
        store.save(session)
        results.append(
            measure(f'storage/save_unchanged_{len(messages)}messages', lambda: store.save(session), 2, rounds)
        )
        if count == 500:
            profiles['save_1001messages'] = profile(lambda: store.save(session))
        for use_context in (False, True):
            capabilities = (
                [ContextViewCapability(strategy=strategy, meter=ContextMeter(window=10_000_000))] if use_context else []
            )
            agent = Agent(FunctionModel(model, model_name='benchmark'), capabilities=capabilities)
            register_file_tools(agent, logger, root=lambda: root)
            results.append(
                await measure_async(
                    f'loop/read_roundtrip_{len(messages)}messages_context{use_context}',
                    lambda agent=agent, messages=messages: agent.run('Read file', message_history=messages),
                    2,
                    rounds,
                )
            )

    # Real local stdio MCP, not mocks and not user-configured servers.
    server_path = root / 'mcp_probe.py'
    server_path.write_text(
        "from mcp.server.mcpserver import MCPServer\nm=MCPServer('probe')\n@m.resource('probe://hello')\ndef hello()->str: return 'hello'\n@m.prompt()\ndef greet(name:str)->str: return 'hello '+name\nm.run()\n"
    )
    spec = MCPServerSpec(
        'probe',
        MCPServerConfig(command=sys.executable, args=[str(server_path)], keep_alive=True),
        root,
        root / 'config.json',
    )
    manager = MCPManager({'probe': spec})
    register_mcp_tools(tools, manager, logger)
    start = time.perf_counter()
    try:
        await tools.tools['list_mcp_servers'](connect=True)
        cold_ms = (time.perf_counter() - start) * 1000
        for name, kwargs in [
            ('list_mcp_servers', {}),
            ('list_mcp_resources', {}),
            ('read_mcp_resource', {'server': 'probe', 'uri': 'probe://hello'}),
            ('list_mcp_prompts', {}),
            ('get_mcp_prompt', {'server': 'probe', 'name': 'greet', 'arguments': {'name': 'Reader'}}),
        ]:
            results.append(
                await measure_async(
                    f'{name}/local_stdio_warm', lambda name=name, kwargs=kwargs: tools.tools[name](**kwargs), 5, rounds
                )
            )
    finally:
        await manager.aclose()

    # Offline web adapters: separately label transport stubs, not internet latency.
    with (
        patch(
            'src.engine.web.tools._search',
            return_value=[{'title': 'Example', 'href': 'https://example.com', 'body': 'Description'}] * 8,
        ),
        patch('src.engine.web.tools._extract', return_value={'content': 'Page text\n' * 4000}),
    ):
        results.append(
            await measure_async(
                'web_search/adapter_only_STUB', lambda: tools.tools['web_search']('benchmark'), 20, rounds
            )
        )
        results.append(
            await measure_async(
                'read_url/adapter_only_STUB', lambda: tools.tools['read_url']('https://example.com'), 20, rounds
            )
        )
    tree = AgentTree()
    tree.ensure_root('benchmark')
    results.append(measure('list_agents/render_root_only', lambda: render_tree_table(tree, None), 100, rounds))
    for size in (100, 1000):
        tree = AgentTree()
        tree.ensure_root('benchmark')
        for _ in range(size):
            tree.add(parent_id=tree.root_id, name='synthetic', role=AgentRole.SUBAGENT)
        results.append(
            measure(
                f'list_agents/render_{size}synthetic_nodes', lambda tree=tree: render_tree_table(tree, None), 3, rounds
            )
        )
    results.extend(await tree_controls(rounds))
    Database.forget(root / 'state' / 'sigil.db')
    return {
        'environment': {
            'python': sys.version,
            'platform': platform.platform(),
            'cpu': platform.processor(),
            'logical_cpus': os.cpu_count(),
            'runtime_source_hashes': {
                relative: hashlib.sha256(source.read_bytes()).hexdigest()
                for relative in (
                    'src/engine/shell/process.py',
                    'src/engine/files/tools.py',
                    'src/engine/files/text.py',
                    'src/engine/context/compaction/projection.py',
                    'src/engine/context/compaction/measurements.py',
                    'src/engine/context/strategy/append_only.py',
                    'src/engine/attachments/tools.py',
                )
                if (source := Path(__file__).resolve().parents[1] / relative).exists()
            },
            'rounds': rounds,
            'git_commit': (
                await asyncio.to_thread(subprocess.check_output, ['git', 'rev-parse', 'HEAD'], text=True)
            ).strip(),
            'mcp_cold_single_sample_ms': cold_ms,
            'notes': 'Warm page cache; GC enabled; sequential checks; ms are per-call round means, not individual latency quantiles. No live LLM, UI, or production config. Shell environment capture bypassed.',
        },
        'results': results,
        'profiles': profiles,
    }


def ambiguous_patch(text: str) -> None:
    try:
        apply_string_patch(text, 'a', 'b', False)
    except FileToolError:
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--label', default='working-tree')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 20:
        parser.error('--rounds must be between 1 and 20')
    with tempfile.TemporaryDirectory(prefix='sigil-core-benchmark-') as directory:
        report = asyncio.run(main(Path(directory), args.rounds))
    report['environment']['label'] = args.label
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    for result in report['results']:
        print(f'{result["label"]:65} {result["median_ms"]:10.3f} ms  [{result["min_ms"]:.3f}, {result["max_ms"]:.3f}]')
