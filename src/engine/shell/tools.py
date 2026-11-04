"""`run` tool: parallel shell commands for the model."""

from __future__ import annotations

from typing import Any

from src.engine.shell.limits import DEFAULT_TIMEOUT
from src.engine.shell.models.run_tool_output import RunToolOutput
from src.engine.shell.runner import execute_commands
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.registrar import ToolRegistrar


def register_shell_tools(agent: Any, logger: ToolLogger | None = None) -> None:
    """Registers the shell tool on a pydantic-ai agent."""
    tools = ToolRegistrar(agent, logger)

    @tools.tool
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
