"""Built-in output declarations shared by live views and persisted transcripts."""

from __future__ import annotations

from functools import cache
from typing import Any

from pydantic import TypeAdapter

from src.engine.tooling.outputs import OutputPresenter, ToolOutput, readable
from src.engine.tooling.presentation import ToolSummary, human_bytes


def process_output(value: Any) -> ToolOutput:
    from src.engine.shell.models.run_tool_output import RunToolOutput
    from src.engine.shell.tools import describe_run

    entries = value if isinstance(value, list) else [value]
    results = TypeAdapter(list[RunToolOutput]).validate_python(entries)
    blocks: list[str] = []
    for index, result in enumerate(results, 1):
        text = '\n'.join(part.rstrip() for part in (result.stdout, result.stderr) if part)
        if not text:
            text = 'Timed out' if result.timed_out else f'Exit {result.returncode}'
        if result.stdout_truncated or result.stderr_truncated:
            text += '\n… (process output truncated)'
        blocks.append(f'Command {index}\n{text}' if len(results) > 1 else text)
    return ToolOutput('\n\n'.join(blocks), describe_run({}, results))


def file_output(value: Any) -> ToolOutput:
    from src.engine.files.models.file_content import FileContent
    from src.engine.files.tools import describe_read

    result = FileContent.model_validate(value)
    text = f'Binary file ({human_bytes(result.size)}); preview omitted.' if result.binary else result.content
    if result.truncated:
        text += '\n… (file content truncated)'
    return ToolOutput(text, describe_read({}, result))


def write_output(value: Any) -> ToolOutput:
    from src.engine.files.models.write_result import WriteResult
    from src.engine.files.tools import describe_write

    result = WriteResult.model_validate(value)
    summary = describe_write({}, result)
    return ToolOutput(f'{result.path}\n{summary.text}', summary)


def patch_output(value: Any) -> ToolOutput:
    from src.engine.files.models.patch_result import PatchResult
    from src.engine.files.tools import describe_patch

    result = PatchResult.model_validate(value)
    summary = describe_patch({}, result)
    return ToolOutput(f'{result.path}\n{result.preview or summary.text}', summary)


def skill_output(value: Any) -> ToolOutput:
    if not isinstance(value, dict):
        return ToolOutput(readable(value))
    name = str(value.get('name', 'Skill'))
    lines = [name, str(value.get('note', ''))]
    for resource in value.get('resources', [])[:100]:
        lines.append(f'  {resource.get("path", "resource")}')
    return ToolOutput('\n'.join(line for line in lines if line), ToolSummary(f'{name} loaded'))


def content_output(value: Any) -> ToolOutput:
    if not isinstance(value, dict):
        return ToolOutput(readable(value))
    if value.get('binary') or value.get('format') == 'content':
        return ToolOutput('Binary content; preview omitted.')
    text = readable(value.get('content', ''))
    if value.get('truncated'):
        text += '\n… (content truncated)'
    return ToolOutput(text, ToolSummary('Content received'))


def search_output(value: Any) -> ToolOutput:
    if not isinstance(value, dict):
        return ToolOutput(readable(value))
    results = value.get('results', [])
    blocks = ['\n'.join(str(item.get(key, '')) for key in ('title', 'url', 'snippet')) for item in results[:100]]
    return ToolOutput('\n\n'.join(blocks) or 'No results.', ToolSummary(f'{len(results)} results'))


def prompt_output(value: Any) -> ToolOutput:
    if not isinstance(value, dict):
        return ToolOutput(readable(value))
    messages = value.get('messages', [])
    lines = [f'{item.get("role", "message")}\n{item.get("content", "")}' for item in messages[:100]]
    return ToolOutput('\n\n'.join(lines), ToolSummary(f'{len(messages)} messages'))


def skills_output(value: Any) -> ToolOutput:
    if not isinstance(value, list):
        return ToolOutput(readable(value))
    lines = [f'{item.get("name", "Skill")}\n  {item.get("description", "")}' for item in value[:100]]
    return ToolOutput('\n'.join(lines) or 'No skills found.', ToolSummary(f'{len(value)} skills'))


def roadmap_output(value: Any) -> ToolOutput:
    if not isinstance(value, dict):
        return ToolOutput(readable(value))
    items = value.get('items', [])
    lines = [f'[{item.get("status", "pending")}] {item.get("title", "")}' for item in items[:100]]
    if value.get('persistence_error'):
        lines.append(f'Could not save plan: {value["persistence_error"]}')
    return ToolOutput('\n'.join(lines) or 'Plan cleared.', ToolSummary(f'{len(items)} tasks'))


@cache
def builtin_presenters() -> dict[str, OutputPresenter]:
    return {
        'run': process_output,
        'run_skill_script': process_output,
        'read_file': file_output,
        'write_file': write_output,
        'patch_file': patch_output,
        'load_skill': skill_output,
        'list_skills': skills_output,
        'update_roadmap': roadmap_output,
        'read_mcp_resource': content_output,
        'get_mcp_prompt': prompt_output,
        'read_skill_resource': content_output,
        'read_url': content_output,
        'web_search': search_output,
    }
