"""Tool `ask_user`: preguntas de opción múltiple dentro de la UI."""

from __future__ import annotations

from typing import Any

from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.models import QuestionItem, QuestionResult
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.registrar import ToolRegistrar


def register_interaction_tools(agent: Any, hub: QuestionHub, logger: ToolLogger | None = None) -> None:
    """Registra la tool de preguntas en un agente de pydantic-ai."""
    tools = ToolRegistrar(agent, logger)

    @tools.tool
    async def ask_user(questions: list[QuestionItem]) -> QuestionResult:
        """Ask the user one or more multiple-choice questions in the UI.

        Use this when requirements are materially ambiguous or an action needs
        explicit authorization. Each question offers 2-3 options; the first (or
        the one marked `recommended`) is highlighted as recommended. The UI adds
        a final "Other" option so the user can type a custom answer. Questions
        are asked one at a time and all answers are returned together. If the
        user cancels, `cancelled` is true and `answers` is empty.

        Prefer this over guessing when a decision changes the outcome. Do not
        use it for routine choices you can make yourself.

        Args:
            questions: Questions to ask. Each needs a prompt and 2-3 options.
                Mark at most one option as `recommended`; if none is marked,
                the first option is used.
        """
        if not questions:
            raise ValueError('`questions` cannot be empty.')
        answers = await hub.ask(questions)
        if answers is None:
            return QuestionResult(answers=[], cancelled=True)
        return QuestionResult(answers=answers)
