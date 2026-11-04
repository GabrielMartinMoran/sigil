"""Tests headless de la pantalla modal de preguntas."""

import asyncio

from textual.app import App

from src.engine.tools.interaction import QuestionAnswer, QuestionItem, QuestionOption
from src.ui.widgets.question_screen import CUSTOM_ID, QuestionScreen


def _question(text: str = '¿Qué hacemos?') -> QuestionItem:
    return QuestionItem(
        question=text,
        options=[
            QuestionOption(label='Recomendada', description='la mejor', recommended=True),
            QuestionOption(label='Alternativa A'),
            QuestionOption(label='Alternativa B'),
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
            assert app.result[0].answer == 'Recomendada'
            assert app.result[0].custom is False

    _run(inner())


def test_custom_option_opens_input():
    async def inner():
        app = Host([_question()])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            # 3 opciones + 'Otro' -> bajar 3 veces
            for _ in range(3):
                await pilot.press('down')
            await pilot.press('enter')
            await pilot.pause()
            custom = app.screen.query_one('#question-custom')
            assert custom.display is True
            await pilot.press('h', 'o', 'l', 'a')
            await pilot.press('enter')
            await pilot.pause()
            assert isinstance(app.result, list)
            assert app.result[0].answer == 'hola'
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
        app = Host([_question('Primera'), _question('Segunda')])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press('enter')
            await pilot.pause()
            await pilot.press('enter')
            await pilot.pause()
            assert isinstance(app.result, list)
            assert [answer.question for answer in app.result] == ['Primera', 'Segunda']
            assert all(answer.answer == 'Recomendada' for answer in app.result)

    _run(inner())


def test_custom_option_id_is_defined():
    assert CUSTOM_ID == '__custom__'
