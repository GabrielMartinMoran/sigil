"""`run` tool: parallel shell commands for the model."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from src.engine.shell.limits import DEFAULT_TIMEOUT
from src.engine.shell.models.run_tool_output import RunToolOutput
from src.engine.shell.runner import execute_commands
from src.engine.tooling.catalog import ToolCatalog
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.permission import ResourceKind, ToolGroup, ToolPermission
from src.engine.tooling.presentation import ToolSummary
from src.engine.tooling.registrar import ToolRegistrar

_SHELL = ToolPermission(groups=(ToolGroup.RUN,), resource='cmds', kind=ResourceKind.COMMAND)

_TEST_SUMMARY = re.compile(r'\b\d+\s+(passed|failed|error|errors|skipped|xfailed|xpassed)\b')
"""The last line of a test runner, which is the only line worth reading.

Deliberately narrow: it matches the summary a runner prints (`9 passed in 0.45s`,
`1 failed, 2 passed`), not any line that happens to contain a number. When it does
not match — a linter, a build, `git status` — the summary falls back to the exit code,
which is always true.
"""


def describe_run(args: Mapping[str, Any], results: Any) -> ToolSummary | None:
    """How a command batch went: `9 passed in 0.45s`, `exit 1`, `2 cmds · 1 failed`.

    `run` takes several commands and returns one result per command, so the summary
    has two shapes. One command gets the detail, because that is the common case and
    the detail is the point. Several get a count: the per-command lines belong in the
    expanded output, where each result keeps its own.
    """
    if not isinstance(results, list) or not results:
        return None
    if not all(isinstance(result, RunToolOutput) for result in results):
        return None
    if len(results) > 1:
        failed = sum(1 for result in results if result.returncode != 0 or result.timed_out)
        if failed:
            return ToolSummary(f'{len(results)} cmds · {failed} failed', 'bad')
        return ToolSummary(f'{len(results)} cmds · ok', 'good')
    (result,) = results
    if result.timed_out:
        return ToolSummary('timed out', 'bad')
    last = _last_line(result.stdout) or _last_line(result.stderr)
    if last is not None and _TEST_SUMMARY.search(last):
        return ToolSummary(last, 'bad' if result.returncode != 0 else 'good')
    return ToolSummary(f'exit {result.returncode}', 'bad' if result.returncode != 0 else 'good')


def _last_line(text: str | None) -> str | None:
    """The last non-empty line, stripped. `None` when there is nothing to read."""
    for line in reversed((text or '').splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def register_shell_tools(agent: Any, logger: ToolLogger | None = None, catalog: ToolCatalog | None = None) -> None:
    """Registers the shell tool on a pydantic-ai agent."""
    tools = ToolRegistrar(agent, logger, catalog)

    @tools.tool(permission=_SHELL, describe=describe_run)
    async def run(
        cmds: list[str],
        timeout_seconds: int = DEFAULT_TIMEOUT,
    ) -> list[RunToolOutput]:
        """Execute several non-interactive Bash commands in parallel.

        Each command runs independently in its own process; arguments,
        pipes, redirects, and shell scripts are supported. Standard input is
        closed to prevent interactive blocking. Each command is terminated
        when its own timeout expires. Results are returned in the same order
        as `cmds`; a failed or timed-out command is reported in its result
        without affecting the others.

        Args:
            cmds: List of Bash commands to execute in parallel.
            timeout_seconds: Execution timeout in seconds (1-120), applied
                to each command individually.
        """
        return await execute_commands(cmds, timeout_seconds)
