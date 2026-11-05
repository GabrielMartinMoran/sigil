"""`ask_user` tool: multiple-choice questions inside the UI."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.interaction.models.question_result import QuestionResult
from src.engine.tooling.catalog import ToolCatalog
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.permission import ToolGroup, ToolPermission
from src.engine.tooling.registrar import ToolRegistrar

_ASK = ToolPermission(groups=(ToolGroup.ASK,))


def register_interaction_tools(
    agent: Any,
    hub: QuestionHub,
    logger: ToolLogger | None = None,
    catalog: ToolCatalog | None = None,
) -> None:
    """Registers the question tool on a pydantic-ai agent."""
    tools = ToolRegistrar(agent, logger, catalog)

    # `ctx` defaults to None so the tool stays callable from code (tests) where
    # there is no run: pydantic-ai always passes the real context inside a run.
    @tools.context_tool(permission=_ASK)
    async def ask_user(ctx: RunContext[Any] = None, *, questions: list[QuestionItem]) -> QuestionResult:  # type: ignore[assignment]
        """Ask the user one or more multiple-choice questions in the UI.

        Use this when requirements are materially ambiguous or an action needs
        explicit authorization. Each question offers 2-3 options; the first (or
        the one marked `recommended`) is highlighted as recommended. The UI adds
        a final "Other" option so the user can type a custom answer. Questions
        are asked one at a time and all answers are returned together. If the
        user cancels, the turn stops right there and control goes back to them.

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
            # Cancelling is a decision about the whole turn, not an answer to
            # the question: the run stops where it is and the prompt comes back.
            #
            # The result below is still built: depending on where the
            # cancellation lands, the graph may record it in the history as the
            # return of the call. That is fine — a later turn reads it as "the
            # user cancelled" — but the run never answers it and never asks the
            # model what to do next.
            #
            # `ctx` is None only when the tool is called from code (tests),
            # where there is no run to cancel and the result is the whole story.
            if ctx is not None:
                ctx.cancel()
            return QuestionResult(answers=[], cancelled=True)
        return QuestionResult(answers=answers)
