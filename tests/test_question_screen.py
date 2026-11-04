"""Headless tests for the question modal screen."""

import asyncio

from textual.app import App

from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_item import QuestionItem, QuestionOption
from src.ui.widgets.question_screen import CUSTOM_ID, QuestionScreen


def _question(text: str = 'What do we do?') -> QuestionItem:
    return QuestionItem(
        question=text,
        options=[
            QuestionOption(label='Recommended', description='the best one', recommended=True),
            QuestionOption(label='Alternative A'),
            QuestionOption(label='Alternative B'),
        ],
    )


class Host(App):
    def __init__(self, questions: list[QuestionItem]) -> None:
        super().__init__()
        self._questions = questions
        self.result: list[QuestionAnswer] | None | str = 'unset'

    def on_mount(self) -> None:
        self.push_screen(QuestionScreen(self._questions), self._done)

    def _done(self, result: list[QuestionAnswer] | None) -> None:
        self.result = result


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=10))


def test_select_recommended_option():
    async def inner():
        app = Host([_question()])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press('enter')
            await pilot.pause()
            assert isinstance(app.result, list)
            assert app.result[0].answer == 'Recommended'
            assert app.result[0].custom is False

    _run(inner())


def test_custom_option_opens_input():
    async def inner():
        app = Host([_question()])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            # 3 options + 'Other' -> move down 3 times
            for _ in range(3):
                await pilot.press('down')
            await pilot.press('enter')
            await pilot.pause()
            custom = app.screen.query_one('#question-custom')
            assert custom.display is True
            await pilot.press('h', 'e', 'l', 'l', 'o')
            await pilot.press('enter')
            await pilot.pause()
            assert isinstance(app.result, list)
            assert app.result[0].answer == 'hello'
            assert app.result[0].custom is True

    _run(inner())


def test_cancel_returns_none():
    async def inner():
        app = Host([_question()])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press('escape')
            await pilot.pause()
            assert app.result is None

    _run(inner())


def test_multiple_questions_are_asked_in_order():
    async def inner():
        app = Host([_question('First'), _question('Second')])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press('enter')
            await pilot.pause()
            await pilot.press('enter')
            await pilot.pause()
            assert isinstance(app.result, list)
            assert [answer.question for answer in app.result] == ['First', 'Second']
            assert all(answer.answer == 'Recommended' for answer in app.result)

    _run(inner())


def test_custom_option_id_is_defined():
    assert CUSTOM_ID == '__custom__'
