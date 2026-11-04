"""Contract of a history view strategy."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic_ai.messages import ModelMessage

from src.engine.context.budget import ContextBudget
from src.engine.context.strategy.view_result import ViewResult


class ContextStrategy(Protocol):
    """Projects the session log onto what gets sent to the model."""

    def view(self, messages: Sequence[ModelMessage], budget: ContextBudget) -> ViewResult: ...
