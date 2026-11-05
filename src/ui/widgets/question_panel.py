"""Panel that asks the agent's questions under the chat.

The questions are not a modal screen on purpose. The agent asks when a decision
is ambiguous, and the answer usually lives in the conversation: a modal would
cover the very thing that has to be read. The panel borrows height from the chat
and gives it back when it closes, so the rest of the UI stays usable.

Each question keeps its own draft, so you can move between them with
``alt+←`` / ``alt+→`` without losing anything: going back shows the option you
picked or the text you typed.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.interaction.models.question_option import QuestionOption

CUSTOM_ID = '__custom__'
"""Id of the free-text row of the list."""

HELP_TEXT = 'Enter answer · alt+←/→ switch · ctrl+↑/↓ chat · Esc cancel the turn'

_PREVIOUS_KEY = 'alt+left'
_NEXT_KEY = 'alt+right'
_SCROLL_UP_KEY = 'ctrl+up'
_SCROLL_DOWN_KEY = 'ctrl+down'

_SCROLL_LINES = 5
"""Lines the conversation moves per key: enough to read back, not a full page."""

_ANSWER_MARK = '✓'
_MARKER_WIDTH = 2
_QUESTION_PREVIEW = 34
_ANSWER_PREVIEW = 28
_ANSWER_ROWS = 3
"""Answered questions kept on screen: enough to confirm, few enough to fit."""


class QuestionPanel(VerticalScroll):
    """Asks the agent's questions, one at a time, without covering the app.

    It scrolls because the content grows with the question: a long prompt plus
    three described options do not fit in every terminal, and clipping them is
    not an option for something that has to be read before answering.

    ``ask`` is the only entry point: it shows the panel, waits for the answers
    and hides the panel again. ``None`` means the user cancelled, which the tool
    turns into the end of the turn: the panel stops asking, not the conversation.
    """

    DEFAULT_CSS = """
    QuestionPanel {
        display: none;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding(_PREVIOUS_KEY, 'previous_question', 'Previous question', show=False),
        Binding(_NEXT_KEY, 'next_question', 'Next question', show=False),
        Binding('tab', 'switch_field', 'List / text field', show=False),
        Binding(_SCROLL_UP_KEY, 'scroll_conversation(-1)', 'Read up', show=False),
        Binding(_SCROLL_DOWN_KEY, 'scroll_conversation(1)', 'Read down', show=False),
        Binding('escape', 'cancel', 'Cancel', show=False),
    ]

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._questions: list[QuestionItem] = []
        self._index = 0
        # One draft per question, so moving around never loses a decision.
        self._drafts: list[QuestionAnswer | None] = []
        # What is in the free-text field: submitted, half-typed or empty.
        self._texts: list[str] = []
        # Whether the free-text field is open for that question.
        self._custom_open: list[bool] = []
        self._future: asyncio.Future[list[QuestionAnswer] | None] | None = None

    # --- composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id='question-progress')
        yield Static(id='question-title')
        yield Static(id='question-answers')
        yield OptionList(id='question-options')
        yield Input(placeholder='Type your answer…', id='question-custom')
        yield Static(id='question-hint')
        yield Static(HELP_TEXT, id='question-help')

    def on_mount(self) -> None:
        self.query_one('#question-custom', Input).display = False
        self.query_one('#question-answers', Static).display = False
        self.query_one('#question-hint', Static).display = False

    def on_unmount(self) -> None:
        # Nobody is going to answer if the panel goes away: never leave the
        # tool call waiting on a future that can no longer be resolved.
        self._resolve(None)

    # --- entry point -------------------------------------------------------

    async def ask(self, questions: list[QuestionItem]) -> list[QuestionAnswer] | None:
        """Shows the questions and returns the answers, or ``None`` if cancelled."""
        if not questions:
            return []
        self._questions = list(questions)
        self._drafts = [None] * len(self._questions)
        self._texts = [''] * len(self._questions)
        self._custom_open = [False] * len(self._questions)
        self._index = 0
        self._future = asyncio.get_running_loop().create_future()
        self.display = True
        self._paint()
        try:
            return await self._future
        finally:
            self._future = None
            self._questions = []
            self._drafts = []
            self._texts = []
            self._custom_open = []
            self.display = False

    # --- rendering ---------------------------------------------------------

    def _paint(self) -> None:
        item = self._questions[self._index]
        draft = self._drafts[self._index]

        progress = Text()
        progress.append(f'Question {self._index + 1} of {len(self._questions)}', style='dim')
        if draft is not None:
            progress.append(f'  {_ANSWER_MARK} answered', style='green')
        self.query_one('#question-progress', Static).update(progress)
        self.query_one('#question-title', Static).update(Text(item.question, style='bold'))
        self._paint_answers()
        self._paint_options(item, draft)
        self._set_hint('')
        # Each question starts from the top: the prompt is the first thing to
        # read, and the panel may have been scrolled while reading the previous.
        self.scroll_home(animate=False)
        # The keys go where the answer lives: the free-text field when that is
        # what the question is holding open, the list otherwise.
        custom = self.query_one('#question-custom', Input)
        custom.display = self._custom_open[self._index]
        custom.value = self._texts[self._index]
        if custom.display:
            custom.focus()
        else:
            self.query_one(OptionList).focus()

    def _paint_answers(self) -> None:
        """What has been answered so far: the record survives moving around.

        Only the last few fit: the point is to confirm what was decided, not to
        redraw the whole form. The panel has a bounded height on purpose.
        """
        answered = [(index, draft) for index, draft in enumerate(self._drafts) if draft is not None]
        hidden = len(answered) - _ANSWER_ROWS
        summary = Text(no_wrap=True, overflow='ellipsis')
        if hidden > 0:
            summary.append(f'… {hidden} more answered\n', style='dim')
        for index, draft in answered[-_ANSWER_ROWS:]:
            question = _clip(self._questions[index].question, _QUESTION_PREVIEW)
            if summary:
                summary.append('\n')
            summary.append(f'{_ANSWER_MARK} {index + 1}. {question} → ', style='dim green')
            summary.append(_clip(draft.answer, _ANSWER_PREVIEW), style='green')
        widget = self.query_one('#question-answers', Static)
        widget.update(summary)
        widget.display = bool(summary)

    def _paint_options(self, item: QuestionItem, draft: QuestionAnswer | None) -> None:
        ordered = _ordered_options(item)
        answered = draft.answer if draft is not None and not draft.custom else None
        options = self.query_one(OptionList)
        options.clear_options()
        for index, option in enumerate(ordered):
            options.add_option(Option(_option_row(option, chosen=option.label == answered), id=f'opt-{index}'))
        if item.allow_custom:
            label = Text(' ' * _MARKER_WIDTH)
            if draft is not None and draft.custom:
                label.append(f'{_ANSWER_MARK} ')
            label.append('Other (type your own)…', style='italic')
            options.add_option(Option(label, id=CUSTOM_ID))
        options.highlighted = _chosen_index(ordered, item, draft, custom_open=self._custom_open[self._index])

    def _set_hint(self, text: str) -> None:
        hint = self.query_one('#question-hint', Static)
        hint.update(Text(text, style='yellow'))
        hint.display = bool(text)

    # --- answering ---------------------------------------------------------

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == CUSTOM_ID:
            # The free-text row only opens the field: what was typed before is
            # still there, ready to be edited.
            custom = self.query_one('#question-custom', Input)
            self._custom_open[self._index] = True
            custom.display = True
            custom.value = self._texts[self._index]
            custom.focus()
            return
        ordered = _ordered_options(self._questions[self._index])
        self._record(ordered[_option_index(event.option.id)].label, custom=False)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input is not self.query_one('#question-custom', Input):
            return
        text = event.value.strip()
        if not text:
            self._set_hint('Type an answer, or pick one of the options.')
            return
        self._record(text, custom=True)

    def _record(self, answer: str, *, custom: bool) -> None:
        """Stores the answer of the current question and moves on."""
        item = self._questions[self._index]
        self._drafts[self._index] = QuestionAnswer(question=item.question, answer=answer, custom=custom)
        self._texts[self._index] = answer if custom else ''
        # The field belongs to the answer: picking an option closes it, so going
        # back does not show a text box for an option that was already chosen.
        self._custom_open[self._index] = custom
        self._advance()

    def _stash_text(self) -> None:
        """Keeps what is in the field, even if it was not submitted.

        Reading it here, while the question is still the current one, is what
        makes going back and forth lossless.
        """
        custom = self.query_one('#question-custom', Input)
        if custom.display:
            self._texts[self._index] = custom.value

    # --- movement ----------------------------------------------------------

    def action_previous_question(self) -> None:
        """Goes back. The draft of the question you leave is kept."""
        if self._index == 0:
            self._set_hint('This is the first question.')
            return
        self._stash_text()
        self._index -= 1
        self._paint()

    def action_next_question(self) -> None:
        """Goes forward, or finishes when it is the last question.

        Moving is free in both directions: each question keeps its own draft, and
        the panel only closes when every one of them has an answer.
        """
        if self._index + 1 < len(self._questions):
            self._stash_text()
            self._index += 1
            self._paint()
            return
        self._finish()

    def action_switch_field(self) -> None:
        """Tab: moves between the list and the free-text field."""
        custom = self.query_one('#question-custom', Input)
        if not custom.display:
            return
        options = self.query_one(OptionList)
        if self.screen.focused is custom:
            options.focus()
        else:
            custom.focus()

    def action_scroll_conversation(self, direction: int) -> None:
        """Reads the conversation without leaving the panel.

        The keys are the panel's, so they would otherwise move the list: the
        answer usually depends on what is written above, and shrinking the panel
        to scroll the chat instead would defeat the point.
        """
        pane = self._reading_pane()
        if pane is None:
            return
        step = _SCROLL_LINES if direction > 0 else -_SCROLL_LINES
        target = max(0, int(pane.scroll_offset.y) + step)
        pane.scroll_to(y=target, animate=False)
        # Moving away from the bottom releases the anchor, so a message that
        # arrives while reading does not drag the view down.
        if direction < 0:
            pane.release_anchor()

    def _reading_pane(self) -> VerticalScroll | None:
        """What the ctrl+arrows scroll: the chat, or the debug view when it is open.

        The debug view replaces the chat, so only one of them is on screen.
        """
        candidates = (('#chat', '#chat'), ('#debug', '#debug-scroll'))
        for holder_selector, pane_selector in candidates:
            try:
                holder = self.screen.query_one(holder_selector)
                pane = self.screen.query_one(pane_selector, VerticalScroll)
            except NoMatches:
                continue
            if holder.display:
                return pane
        return None

    def action_cancel(self) -> None:
        """Esc: the way out of a question you do not want to answer.

        It ends the turn instead of picking something for you, and it is a single
        press on purpose: the panel's own help line says so, and a confirmation
        on top of a panel that is already asking something would be a second
        question to answer before the first one closes.

        The two-press abort of the chat (`SigilApp.request_abort`) is for the
        prompt, where `Esc` has no other meaning while the agent works.
        """
        self._resolve(None)

    def _advance(self) -> None:
        """After answering: closes when nothing is left, else moves to what is.

        The move follows the plan order and wraps around, so answering out of
        order still ends on the question that was skipped.
        """
        if all(draft is not None for draft in self._drafts):
            self._resolve([draft for draft in self._drafts if draft is not None])
            return
        for offset in range(1, len(self._questions) + 1):
            index = (self._index + offset) % len(self._questions)
            if self._drafts[index] is None:
                self._index = index
                self._paint()
                return

    def _finish(self) -> None:
        """Closes the panel, or points at the unanswered question that is missing."""
        missing = [index for index, draft in enumerate(self._drafts) if draft is None]
        if missing:
            self._index = missing[0]
            self._paint()
            self._set_hint(f'{len(missing)} question(s) left without an answer.')
            return
        self._resolve([draft for draft in self._drafts if draft is not None])

    def _resolve(self, answers: list[QuestionAnswer] | None) -> None:
        future = self._future
        if future is not None and not future.done():
            future.set_result(answers)


# --- helpers ---------------------------------------------------------------


def _option_row(option: QuestionOption, *, chosen: bool) -> Text:
    """One row of the list: marker, label, description and recommendation."""
    row = Text()
    row.append(f'{_ANSWER_MARK} ' if chosen else ' ' * _MARKER_WIDTH, style='green bold')
    row.append(option.label, style='bold' if chosen else '')
    if option.description:
        row.append(f'  — {option.description}', style='dim')
    if option.recommended:
        row.append('  [recommended]', style='bold cyan')
    return row


def _ordered_options(item: QuestionItem) -> list[QuestionOption]:
    """The recommended option first; the first one is recommended when none is marked."""
    recommended = [option for option in item.options if option.recommended]
    others = [option for option in item.options if not option.recommended]
    if not recommended and item.options:
        recommended = [item.options[0]]
        others = item.options[1:]
    return recommended + others


def _chosen_index(
    ordered: list[QuestionOption],
    item: QuestionItem,
    draft: QuestionAnswer | None,
    *,
    custom_open: bool,
) -> int:
    """Where the highlight starts: on the previous answer, when there is one."""
    if draft is not None and not draft.custom:
        for index, option in enumerate(ordered):
            if option.label == draft.answer:
                return index
    if draft is not None and draft.custom or custom_open:
        return len(ordered) if item.allow_custom else 0
    return 0


def _option_index(option_id: str | None) -> int:
    """``opt-<n>`` → ``n``."""
    assert option_id is not None
    return int(option_id.split('-', 1)[1])


def _clip(text: str, limit: int) -> str:
    """One line, cut at ``limit`` characters."""
    flat = ' '.join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + '…'
