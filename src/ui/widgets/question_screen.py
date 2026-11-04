"""Pantalla modal para hacer preguntas al usuario."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from src.engine.interaction.models import QuestionAnswer, QuestionItem, QuestionOption

CUSTOM_ID = '__custom__'


class QuestionScreen(ModalScreen[list[QuestionAnswer] | None]):
    """Devuelve las respuestas o ``None`` si se cancela."""

    BINDINGS: ClassVar[list[Binding]] = [Binding('escape', 'cancel', 'Cancel')]

    def __init__(self, questions: list[QuestionItem]) -> None:
        super().__init__()
        self._questions = questions
        self._index = 0
        self._answers: list[QuestionAnswer] = []
        self._labels: list[str] = []

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(id='question-progress'),
            Static(id='question-title'),
            OptionList(id='question-options'),
            Input(placeholder='Type your answer…', id='question-custom'),
            Static('↑/↓ select · Enter confirm · Esc cancel', id='question-help'),
            id='question-box',
        )

    def on_mount(self) -> None:
        self.query_one('#question-custom', Input).display = False
        if not self._questions:
            self.dismiss([])
            return
        self._render_question()

    def _render_question(self) -> None:
        item = self._questions[self._index]
        self.query_one('#question-progress', Static).update(
            f'[dim]Question {self._index + 1} of {len(self._questions)}[/dim]'
        )
        self.query_one('#question-title', Static).update(item.question)

        ordered = _ordered_options(item)
        self._labels = [option.label for option in ordered]
        has_recommended = any(option.recommended for option in ordered)

        options = self.query_one(OptionList)
        options.clear_options()
        for index, option in enumerate(ordered):
            label = option.label
            if option.description:
                label = f'{label}  [dim]— {option.description}[/dim]'
            # La primera opción siempre se muestra como recomendada.
            if option.recommended or (index == 0 and not has_recommended):
                label = f'{label}  [bold cyan][recommended][/bold cyan]'
            options.add_option(Option(label, id=f'opt-{index}'))
        if item.allow_custom:
            options.add_option(Option('[italic]Other (type your own)…[/italic]', id=CUSTOM_ID))
        options.highlighted = 0
        options.focus()

        custom = self.query_one('#question-custom', Input)
        custom.display = False
        custom.value = ''

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == CUSTOM_ID:
            custom = self.query_one('#question-custom', Input)
            custom.display = True
            custom.focus()
            return
        self._record(self._labels[int(str(event.option.id).split('-', 1)[1])], custom=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input is not self.query_one('#question-custom', Input):
            return
        text = event.value.strip()
        if not text:
            return
        self._record(text, custom=True)

    def _record(self, answer: str, *, custom: bool) -> None:
        item = self._questions[self._index]
        self._answers.append(QuestionAnswer(question=item.question, answer=answer, custom=custom))
        self._index += 1
        if self._index >= len(self._questions):
            self.dismiss(self._answers)
        else:
            self._render_question()

    def action_cancel(self) -> None:
        self.dismiss(None)


def _ordered_options(item: QuestionItem) -> list[QuestionOption]:
    recommended = [option for option in item.options if option.recommended]
    others = [option for option in item.options if not option.recommended]
    if not recommended and item.options:
        recommended = [item.options[0]]
        others = item.options[1:]
    return recommended + others
