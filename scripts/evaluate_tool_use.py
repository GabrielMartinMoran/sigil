"""Offline tool-use smoke test or explicit live-model comparison with local fixtures."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import UsageLimits

from src.core.codemode.tools import register_code_tools
from src.core.context.tokens import estimate_message_tokens
from src.core.files.tools import register_file_tools


async def run_offline() -> dict[str, object]:
    """Run one deterministic file workflow through real registered tools."""
    with tempfile.TemporaryDirectory(prefix='sigil-tool-eval-') as directory:
        root = Path(directory)
        requests = 0

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            nonlocal requests
            requests += 1
            if requests == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            'write_file',
                            {'path': 'note.txt', 'operations': [{'type': 'write', 'content': 'evaluation marker'}]},
                        )
                    ]
                )
            if requests == 2:
                return ModelResponse(parts=[ToolCallPart('read_file', {'path': 'note.txt'})])
            return ModelResponse(parts=[TextPart('evaluation complete')])

        agent = Agent(FunctionModel(model))
        register_file_tools(agent, root=lambda root=root: root)
        start = time.perf_counter()
        result = await agent.run('Create and read note.txt containing the evaluation marker.')
        duration_ms = (time.perf_counter() - start) * 1000
        messages = result.all_messages()
        responses = [message for message in messages if isinstance(message, ModelResponse)]
        calls = [part for message in responses for part in message.parts if isinstance(part, ToolCallPart)]
        rounds = sum(any(isinstance(part, ToolCallPart) for part in message.parts) for message in responses)
        retries = [part for message in messages for part in message.parts if isinstance(part, RetryPromptPart)]
        errors = sum(isinstance(part.content, list) for part in retries)
        read_results = [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == 'read_file'
        ]
        read_succeeded = any(getattr(value, 'content', None) == 'evaluation marker' for value in read_results)
        sample = {
            'case': 'write-and-read-v1',
            'success': (root / 'note.txt').read_text() == 'evaluation marker' and read_succeeded,
            'tool_retries': len(retries),
            'requests': requests,
            'tool_rounds': rounds,
            'tool_calls': len(calls),
            'argument_errors': errors,
            'duration_ms': duration_ms,
            'estimated_history_tokens': sum(estimate_message_tokens(message) for message in messages),
        }
        return {'format_version': 1, 'mode': 'scripted-offline', 'model_quality_measured': False, 'samples': [sample]}


async def run_live(model: str, repetitions: int) -> dict[str, object]:
    """Compare direct tools with programmatic access on identical local tasks.

    Requires an explicit provider/model and its normal runtime credentials. This
    command makes paid provider requests; it never loads credential files itself.
    """
    samples = []
    for repetition in range(repetitions):
        for programmatic in (False, True):
            with tempfile.TemporaryDirectory(prefix='sigil-live-eval-') as directory:
                root = Path(directory)
                for index, value in enumerate((7, 13, 29)):
                    (root / f'input-{index}.txt').write_text(str(value))
                agent = Agent(
                    model,
                    instructions=(
                        'Complete the task using the available tools. Only access the supplied temporary directory. '
                        'Do not guess file contents. execute_code, if available, supports top-level await and '
                        'await tools.read_file(path=...) and await tools.write_file(path=..., operations=[...]).'
                    ),
                )
                register_file_tools(agent, root=lambda root=root: root)
                if programmatic:
                    register_code_tools(agent)
                prompt = (
                    f'In {root}, read input-0.txt, input-1.txt, and input-2.txt. '
                    'Create total.txt containing only their integer sum, without whitespace. '
                    'Then read total.txt to verify its contents. Do not modify the input files.'
                )
                sample: dict[str, object] = {
                    'case': 'sum-files-v1',
                    'model': model,
                    'repetition': repetition,
                    'configuration': 'programmatic' if programmatic else 'direct',
                }
                start = time.perf_counter()
                try:
                    async with asyncio.timeout(150):
                        result = await agent.run(
                            prompt, usage_limits=UsageLimits(request_limit=12, tool_calls_limit=64)
                        )
                    messages = result.all_messages()
                    responses = [m for m in messages if isinstance(m, ModelResponse)]
                    calls = [p for m in responses for p in m.parts if isinstance(p, ToolCallPart)]
                    retries = [p for m in messages for p in m.parts if isinstance(p, RetryPromptPart)]
                    success = (root / 'total.txt').is_file() and (root / 'total.txt').read_bytes() == b'49'
                    unchanged = all((root / f'input-{i}.txt').read_text() == str(v) for i, v in enumerate((7, 13, 29)))
                    sample.update(
                        success=success and unchanged,
                        requests=len(responses),
                        tool_rounds=sum(any(isinstance(p, ToolCallPart) for p in m.parts) for m in responses),
                        tool_calls=len(calls),
                        tool_retries=len(retries),
                        argument_errors=sum(isinstance(p.content, list) for p in retries),
                        provider_usage=asdict(result.usage()),
                        estimated_history_tokens=sum(estimate_message_tokens(m) for m in messages),
                    )
                except (RuntimeError, ValueError, OSError, TimeoutError) as error:
                    sample.update(success=False, error_type=type(error).__name__)
                sample['duration_ms'] = (time.perf_counter() - start) * 1000
                samples.append(sample)
    return {
        'format_version': 1,
        'mode': 'live',
        'model_quality_measured': True,
        'limitations': 'One fixed scenario; outer tool calls only. No legacy-schema baseline. File result success does not prove the final verification read occurred.',
        'samples': samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', help='Explicit provider:model; makes live, potentially paid requests.')
    parser.add_argument('--repetitions', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 20:
        parser.error('--repetitions must be between 1 and 20')
    report = asyncio.run(run_live(args.model, args.repetitions) if args.model else run_offline())
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
