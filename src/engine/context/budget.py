"""Context budget: named reservations over the model window.

The window is not all for the history. Before splitting it up you must subtract what
is always sent and what the model needs in order to answer:

    window
      - tools (schemas)          <- measured, usually the largest block
      - instructions (blocks)    <- measured
      - output (max_tokens)      <- reserved
      = available for history
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_OUTPUT_RESERVE = 8_000
"""Tokens left free for the model response."""


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Split of the context window between fixed blocks and history."""

    window: int | None
    """Model window, or ``None`` if unknown."""

    tools: int = 0
    """Tokens of the tool schemas."""

    instructions: int = 0
    """Tokens of the instruction blocks."""

    output_reserve: int = DEFAULT_OUTPUT_RESERVE
    """Tokens reserved for the model response."""

    @property
    def fixed(self) -> int:
        """What is sent on every request regardless of the conversation."""
        return self.tools + self.instructions

    @property
    def available_for_history(self) -> int | None:
        """Tokens left for the history, or ``None`` if there is no window."""
        if self.window is None:
            return None
        return max(0, self.window - self.fixed - self.output_reserve)

    def fits(self, history_tokens: int) -> bool | None:
        """Whether a history fits in the budget. ``None`` if there is no window."""
        available = self.available_for_history
        if available is None:
            return None
        return self.fixed + self.output_reserve <= self.window and history_tokens <= available

    def with_measurements(self, *, tools: int | None = None, instructions: int | None = None) -> ContextBudget:
        """Copy with the measurements updated."""
        return ContextBudget(
            window=self.window,
            tools=self.tools if tools is None else tools,
            instructions=self.instructions if instructions is None else instructions,
            output_reserve=self.output_reserve,
        )
