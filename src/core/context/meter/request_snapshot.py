"""The RequestSnapshot value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.context.meter.instruction_row import InstructionRow
from src.core.context.meter.prompt_section import PromptSection
from src.core.context.meter.tool_row import ToolRow


@dataclass(frozen=True, slots=True)
class RequestSnapshot:
    """Fixed sections and content-free history costs of the last prepared request."""

    instructions: tuple[InstructionRow, ...] = ()
    tools: tuple[ToolRow, ...] = ()
    native_tools: int = 0
    """Native provider tools (they do not travel as a JSON schema)."""

    sections: tuple[PromptSection, ...] = ()
    history_captured: bool = False
    skills: dict[str, int] = field(default_factory=dict)
    captured: bool = False
    """Whether the snapshot comes from a real request. When false, the zeros are not a measurement.

    It happens in a freshly loaded session with `/load`: there is history, but no
    request ran in this process and therefore there are no observed instructions or
    tools.
    """

    @property
    def instructions_tokens(self) -> int:
        return sum(row.tokens for row in self.instructions)

    @property
    def tools_tokens(self) -> int:
        return sum(row.tokens for row in self.tools)

    @property
    def dynamic_instructions(self) -> tuple[InstructionRow, ...]:
        """Blocks recomputed per request: candidates to invalidate the cache."""
        return tuple(row for row in self.instructions if row.dynamic)
