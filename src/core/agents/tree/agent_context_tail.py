"""The AgentContextTail value of the agents layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import RunContext

from src.core.agents.tree.render_tail import render_tail

TAIL_MARKER = 'sigil_agent_context_tail'


@dataclass
class AgentContextTail(AbstractCapability[Any]):
    """Add current agent state to the outgoing request, never to the transcript."""

    node_blocks: Callable[[str | None], str]

    async def before_model_request(
        self, ctx: RunContext[Any], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        node_id = getattr(ctx.deps, 'node_id', None)
        content = render_tail(self.node_blocks(node_id))
        if not content:
            return request_context
        messages = list(request_context.messages)
        messages.append(ModelRequest(parts=[UserPromptPart(content=content)], metadata={TAIL_MARKER: True}))
        request_context.messages = messages
        return request_context
