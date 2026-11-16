"""Programmatic tool calls through an isolated Python interpreter."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai import RunContext

from src.core.codemode.dispatch import NestedDispatcher
from src.core.codemode.sandbox import DEFAULT_TIMEOUT_SECONDS, SandboxResult, execute_python
from src.core.tooling.compaction_policies import EXECUTE_CODE_COMPACTOR
from src.core.tooling.logging import ToolLogger
from src.core.tooling.tool_catalog import ToolCatalog
from src.core.tooling.tool_group import ToolGroup
from src.core.tooling.tool_permission import ToolPermission
from src.core.tooling.tool_registrar import ToolRegistrar

_NESTED_TOOLS = frozenset({'read_file', 'write_file', 'web_search', 'read_url'})


def register_code_tools(agent: Any, logger: ToolLogger | None = None, catalog: ToolCatalog | None = None) -> None:
    """Register isolated Python execution with permission-aware tool access."""
    tools = ToolRegistrar(agent, logger, catalog)

    @tools.context_tool(permission=ToolPermission(groups=(ToolGroup.RUN,)), compaction=EXECUTE_CODE_COMPACTOR)
    async def execute_code(
        ctx: RunContext[Any],
        code: str,
        timeout_seconds: Annotated[int, Field(ge=1, le=120)] = DEFAULT_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Extract, transform, and combine tool results using isolated async Python.

        Filter, join, deduplicate, aggregate, or calculate before printing relevant
        evidence, avoiding full tool responses in model context and intermediate
        model round trips. Prefer a direct call when no processing is needed.
        Preserve source references, errors, and truncation notices in summaries.

        Supported nested tools: read_file, write_file, web_search, read_url, only
        when available and permitted by the active profile. Their schemas apply;
        call with keyword arguments. Top-level await is supported. For independent
        reads, for example:
            import asyncio
            results = await asyncio.gather(
                tools.read_file(path='src/alpha.py'),
                tools.read_file(path='src/beta.py'),
            )
            for result in results:
                print(result['path'], result['size'], result['truncated'])
        Select fields or matching content for the task; serialize dependent calls.

        Direct workspace and network access are unavailable; use nested tools for
        those resources. System runtime files are read-only. Linux, bubblewrap,
        and systemd are required. Other tools and recursive execution are unsupported.
        stdout and stderr are returned with limits. A timeout cancels the script
        and pending async calls, not completed effects; already running synchronous
        calls may continue. Inspect effects before retrying writes after a failure.

        Args:
            code: Python source to run, including optional top-level await.
            timeout_seconds: Total time limit in seconds (1–120, default 120).
        """
        dispatcher = NestedDispatcher(ctx, _NESTED_TOOLS, max_calls=64)
        result: SandboxResult = await execute_python(code, timeout_seconds=timeout_seconds, dispatcher=dispatcher)
        return asdict(result)
