"""Headless tests for the question panel.

The panel is not a modal: it lives under the chat and borrows its height, so the
answers can be about something that is still readable. These tests check the
navigation between questions and that a draft is not lost when going back.
"""

import asyncio

from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList, Static

from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_item import QuestionItem, QuestionOption
from src.ui.widgets.question_panel import CUSTOM_ID, QuestionPanel


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
    """Minimal app with the panel, to drive it with the pilot."""

    CSS = """
    QuestionPanel {
        height: auto;
        max-height: 60%;
    }
    """

    def __init__(self, questions: list[QuestionItem]) -> None:
        super().__init__()
        self._questions = questions
        self.result: list[QuestionAnswer] | None | str = 'unset'
        self.panel = QuestionPanel(id='questions')

    def compose(self) -> ComposeResult:
        yield self.panel

    def on_mount(self) -> None:
        self.run_worker(self._ask())

    async def _ask(self) -> None:
        self.result = await self.panel.ask(self._questions)


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=10))


def _title(app: Host) -> str:
    return str(app.panel.query_one('#question-title', Static).render())


def test_select_recommended_option():
    async def inner():
        app = Host([_question()])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            assert app.panel.display is True
            await pilot.press('enter')
            await pilot.pause()
            assert isinstance(app.result, list)
            assert app.result[0].answer == 'Recommended'
            assert app.result[0].custom is False
            assert app.panel.display is False

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
            custom = app.panel.query_one('#question-custom', Input)
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
            assert app.panel.display is False

    _run(inner())


def test_multiple_questions_are_asked_in_order():
    async def inner():
        app = Host([_question('First'), _question('Second')])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            assert 'First' in _title(app)
            await pilot.press('enter')
            await pilot.pause()
            assert 'Second' in _title(app)
            await pilot.press('enter')
            await pilot.pause()
            assert isinstance(app.result, list)
            assert [answer.question for answer in app.result] == ['First', 'Second']
            assert all(answer.answer == 'Recommended' for answer in app.result)

    _run(inner())


def test_going_back_shows_the_answer_that_was_chosen():
    async def inner():
        app = Host([_question('First'), _question('Second')])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            # Second option of the first question.
            await pilot.press('down')
            await pilot.press('enter')
            await pilot.pause()
            assert 'Second' in _title(app)

            await pilot.press('alt+left')
            await pilot.pause()
            assert 'First' in _title(app)
            # The record of what was answered and the mark on the chosen row.
            summary = str(app.panel.query_one('#question-answers', Static).render())
            assert 'Alternative A' in summary
            options = app.panel.query_one(OptionList)
            assert options.highlighted == 1
            assert '✓' in str(options.get_option_at_index(1).prompt)

    _run(inner())


def test_going_back_keeps_the_custom_text():
    async def inner():
        app = Host([_question('First'), _question('Second')])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            for _ in range(3):
                await pilot.press('down')
            await pilot.press('enter')
            await pilot.pause()
            await pilot.press('a', 'b', 'c')
            await pilot.press('enter')
            await pilot.pause()
            assert 'Second' in _title(app)

            await pilot.press('alt+left')
            await pilot.pause()
            custom = app.panel.query_one('#question-custom', Input)
            assert custom.display is True
            assert custom.value == 'abc'

    _run(inner())


def test_changing_an_answer_replaces_it():
    async def inner():
        app = Host([_question('First'), _question('Second')])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press('enter')  # first question: Recommended
            await pilot.pause()
            await pilot.press('alt+left')
            await pilot.pause()
            await pilot.press('down')
            await pilot.press('enter')  # first question again: Alternative A
            await pilot.pause()
            assert 'Second' in _title(app)
            await pilot.press('enter')
            await pilot.pause()
            assert isinstance(app.result, list)
            assert app.result[0].answer == 'Alternative A'

    _run(inner())


def test_moving_forward_is_free_but_finishing_requires_every_answer():
    async def inner():
        app = Host([_question('First'), _question('Second')])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            # Forward is free: nothing is lost, the first question keeps its draft.
            await pilot.press('alt+right')
            await pilot.pause()
            assert 'Second' in _title(app)
            await pilot.press('alt+left')
            await pilot.pause()
            assert 'First' in _title(app)

            # Finishing without answering points at what is missing.
            await pilot.press('alt+right')
            await pilot.pause()
            await pilot.press('alt+right')
            await pilot.pause()
            assert 'First' in _title(app)
            assert app.result == 'unset'
            assert app.panel.query_one('#question-hint', Static).display is True

    _run(inner())


def test_finishing_the_last_question_with_one_unanswered_goes_back_to_it():
    async def inner():
        app = Host([_question('First'), _question('Second')])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press('alt+right')  # to the second, first still open
            await pilot.pause()
            await pilot.press('enter')  # answer the second
            await pilot.pause()
            # It lands on the one that is still open, not on a closed panel.
            assert 'First' in _title(app)
            assert app.result == 'unset'
            await pilot.press('enter')
            await pilot.pause()
            assert isinstance(app.result, list)
            assert [answer.question for answer in app.result] == ['First', 'Second']

    _run(inner())


def test_previous_on_the_first_question_explains_itself():
    async def inner():
        app = Host([_question('First'), _question('Second')])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            await pilot.press('alt+left')
            await pilot.pause()
            assert 'First' in _title(app)
            assert app.result == 'unset'

    _run(inner())


def test_going_back_keeps_text_that_was_not_submitted():
    async def inner():
        app = Host([_question('First'), _question('Second')])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            for _ in range(3):
                await pilot.press('down')
            await pilot.press('enter')  # Other
            await pilot.pause()
            await pilot.press('h', 'a', 'l', 'f')  # typed, not submitted
            await pilot.press('alt+right')  # last question: it tries to finish
            await pilot.pause()
            assert app.result == 'unset'
            await pilot.press('alt+left')
            await pilot.pause()
            custom = app.panel.query_one('#question-custom', Input)
            assert custom.display is True
            assert custom.value == 'half'

    _run(inner())


def test_tab_moves_between_the_list_and_the_text_field():
    async def inner():
        app = Host([_question()])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            for _ in range(3):
                await pilot.press('down')
            await pilot.press('enter')  # Other: the field takes the focus
            await pilot.pause()
            custom = app.panel.query_one('#question-custom', Input)
            assert app.focused is custom
            await pilot.press('tab')
            await pilot.pause()
            assert app.focused is app.panel.query_one(OptionList)
            await pilot.press('tab')
            await pilot.pause()
            assert app.focused is custom

    _run(inner())


def test_picking_an_option_after_typing_closes_the_field():
    async def inner():
        app = Host([_question()])
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            for _ in range(3):
                await pilot.press('down')
            await pilot.press('enter')
            await pilot.pause()
            await pilot.press('h', 'i')
            await pilot.press('tab')  # back to the list, keeping the text
            await pilot.pause()
            await pilot.press('home')  # first option
            await pilot.press('enter')
            await pilot.pause()
            assert isinstance(app.result, list)
            assert app.result[0].answer == 'Recommended'
            assert app.result[0].custom is False

    _run(inner())


def test_custom_option_id_is_defined():
    assert CUSTOM_ID == '__custom__'
