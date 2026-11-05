"""Scrollable chat history with auto-scroll anchored to the bottom.

Three kinds of block live here, and each one answers a different reading mode:

- **Assistant text** is Markdown, because the model writes Markdown and rendering it
  is most of the value of a chat UI.
- **Reasoning** is collapsed into a single marker line by default. It is the part of
  the transcript nobody reads twice, and left expanded it buries the answer. The
  marker stays so the turn is honest about having thought before answering, and
  `/reasoning-visibility on` expands it in place, without losing the conversation.
- **Tool calls** are drawn as a small block: the call on the header, its output
  indented under a gutter on the left. The shape is the one Codex uses, and it works
  because the indentation *is* the grouping: a run of tool calls reads as one
  indented column under the answer that caused it, instead of a list of unrelated
  lines that all start at the left margin.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from src.engine.attachments.content import describe_attachments, images_in
from src.engine.attachments.image_attachment import ImageAttachment
from src.engine.context.images import image_size
from src.ui.palette import color

RENDER_INTERVAL = 0.08
"""Seconds between Markdown re-renders while streaming.

Re-parsing on every delta is wasted work: the document changes by a few characters
and the parser walks the whole thing. The final flush is guaranteed by `finalize()`.
"""

GUTTER = '  │ '
"""Left edge of a tool block, on every line.

One continuous bar instead of a corner plus indentation: it draws the block as a box
on the left, which is what makes a run of calls read as one group hanging under the
answer that caused it, and it needs no closing line to look finished.
"""

INDENT = '    '
"""Indentation for content under a line that is not a tool block."""

TOOL_OUTPUT_LINES = 6
"""Output lines shown before the block truncates. Enough to recognize the result."""

TOOL_OUTPUT_WIDTH = 160
"""Characters per output line kept before the block truncates a long one."""

REASONING_MARKER = '✳'
"""Marker of the collapsed reasoning line. Not an arrow: it is not an action."""

IMAGE_MARK = '▣'
"""Marker of the attachment summary line: a picture, in one column."""


def preview(value: Any, limit: int = TOOL_OUTPUT_WIDTH) -> str:
    """One line, whitespace collapsed, truncated with an ellipsis."""
    try:
        text = ' '.join(str(value).split())
    except Exception:  # noqa: BLE001 - a broken `__str__` must not break the chat
        text = object.__repr__(value)
    return text if len(text) <= limit else text[: limit - 1] + '…'


def format_tool_args(args: Any) -> str:
    """The arguments of a call as a compact, readable line.

    A dict is rendered as ``key=value`` pairs rather than JSON: the braces and the
    quotes cost columns and say nothing a reader needs. A single argument is shown
    on its own, which is what makes `run(command='ls')` read as `run ls`.
    """
    if isinstance(args, dict):
        if not args:
            return ''
        if len(args) == 1:
            (value,) = args.values()
            return preview(value)
        return ', '.join(f'{key}={preview(value, 48)}' for key, value in args.items())
    return preview(args)


def _user_renderable(text: str, *, queued: bool, attachments: Sequence[ImageAttachment] = ()) -> Text:
    """One user line. A queued one says out loud that the agent has not read it.

    The `[Image #n]` marker is printed as it was written, because it is what the
    model reads and therefore what a later question will refer to. The line under
    it says what the images are; a screenshot in the transcript has no other way
    of showing that the model received pixels and not a filename.
    """
    line = Text(f'› {text}', style='bold')
    if queued:
        line.append('  · queued for the running turn', style='dim italic')
    described = describe_attachments(attachments)
    if described:
        line.append(f'\n  {IMAGE_MARK} {described}', style='dim')
    return line


def output_lines(value: Any) -> list[str]:
    """The lines of a tool result, ready to be indented under the gutter.

    Leading whitespace is kept: a diff, a stack trace or a snippet of code only
    reads correctly with its indentation, and a tool that answers with code is the
    common case. Trailing whitespace is dropped — it is invisible and costs width.
    """
    try:
        raw = value if isinstance(value, str) else str(value)
    except Exception:  # noqa: BLE001 - see `preview`
        raw = object.__repr__(value)
    lines = [line.rstrip() for line in raw.splitlines()]
    # A trailing blank line is an artefact of `print`-style output, not content.
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _clip(line: str, limit: int = TOOL_OUTPUT_WIDTH) -> str:
    """Truncates a line without touching its indentation."""
    return line if len(line) <= limit else line[: limit - 1] + '…'


def truncate_output(lines: list[str], limit: int = TOOL_OUTPUT_LINES) -> tuple[list[str], int]:
    """Head of ``lines`` plus how many were dropped. Returns ``(shown, omitted)``."""
    shown = [_clip(line) for line in lines[:limit]]
    return shown, max(0, len(lines) - len(shown))


def _describe_parts(images: Sequence[BinaryContent]) -> tuple[ImageAttachment, ...]:
    """Rebuilds the summary of images that came back from a stored history.

    The parts do not carry a pixel size the way the composer knew it, so the
    dimensions are read back from the payload header. It is the same information
    the live turn printed: without it a reloaded conversation would show the
    marker and nothing that says a picture travelled with it.
    """
    described: list[ImageAttachment] = []
    for number, part in enumerate(images, start=1):
        width, height = image_size(part.data)
        described.append(
            ImageAttachment(
                number=number,
                data=part.data,
                media_type=str(part.media_type),
                width=width,
                height=height,
            )
        )
    return tuple(described)


def _user_widget(content: str | list[Any]) -> Static:
    """One user line, from a plain prompt or from text plus images.

    Both shapes reach `render_history`: a turn typed before images existed saved
    its prompt as a string, and one with a pasted screenshot saved it as a list of
    text and `BinaryContent`. The marker `[Image #n]` is already in the text, so
    the images only have to be summarised back into the line under it.
    """
    if isinstance(content, str):
        return Static(_user_renderable(content, queued=False), classes='user-msg')
    text = ' '.join(item for item in content if isinstance(item, str))
    described = _describe_parts(images_in(content))
    return Static(_user_renderable(text, queued=False, attachments=described), classes='user-msg')


class AssistantBlock(Static):
    """One model response block: collapsed reasoning plus the Markdown answer.

    The Markdown is re-rendered at most every `RENDER_INTERVAL` seconds so as not
    to re-parse it on every delta; a final flush is guaranteed with `finalize()`.
    """

    def __init__(self, *, reasoning_visible: bool = False, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._thinking = ''
        self._text = ''
        self._reasoning_visible = reasoning_visible
        self._dirty = False
        self._last_render = 0.0
        self._timer: Any = None

    @property
    def reasoning_visible(self) -> bool:
        return self._reasoning_visible

    def set_reasoning_visible(self, visible: bool) -> None:
        """Expands or collapses the reasoning without touching the answer."""
        if visible == self._reasoning_visible:
            return
        self._reasoning_visible = visible
        self._flush()

    def add_thinking(self, delta: str) -> None:
        self._thinking += delta
        self._schedule_refresh()

    def add_text(self, delta: str) -> None:
        self._text += delta
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        elapsed = time.monotonic() - self._last_render
        if elapsed >= RENDER_INTERVAL or not self.is_mounted:
            # Nothing to schedule against before mount: `finalize()` paints.
            self._flush()
            return

        self._dirty = True
        if self._timer is None:
            self._timer = self.set_timer(RENDER_INTERVAL - elapsed, self._on_timer)

    def _on_timer(self) -> None:
        self._timer = None
        if self._dirty:
            self._flush()

    def _flush(self) -> None:
        self._dirty = False
        self._last_render = time.monotonic()
        self._paint()

    def _paint(self) -> None:
        """Pushes the built renderable to the widget, if there is a widget to push to.

        A block is a plain object until it is mounted, and `update()` needs a running
        app. Guarding here keeps the block constructible on its own — which is how
        the tests exercise the rendering — and the first flush after mount catches up.
        """
        if self.is_mounted:
            self.update(self._build())

    def on_mount(self) -> None:
        self._flush()

    def finalize(self) -> None:
        """Flushes the pending content right away (end of the turn)."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._flush()

    def repaint(self) -> None:
        """Rebuilds the content after a theme change; see `ChatLog.repaint`."""
        self._flush()

    # --- rendering ----------------------------------------------------------

    def _build(self):
        parts: list[object] = []
        if self._thinking:
            parts.append(self._reasoning_renderable())
        if self._text:
            parts.append(Markdown(self._text))
        if not parts:
            return Text('')
        if len(parts) == 1:
            return parts[0]
        return Group(*parts)

    def _reasoning_renderable(self):
        """The reasoning, expanded or as a one-line marker.

        The marker is the whole point of the collapsed state: it says the model
        thought, how much it thought, and which command brings it back. Without it
        a collapsed turn looks like a model that answered without reasoning.
        """
        if not self._reasoning_visible:
            return self._collapsed_marker()

        text = Text()
        text.append(f'{REASONING_MARKER} ', style=color(self, 'reasoning'))
        text.append('Reasoning\n', style=f'italic {color(self, "reasoning")}')
        for line in self._thinking.splitlines():
            text.append(INDENT, style=color(self, 'reasoning'))
            text.append(f'{line}\n', style=f'italic {color(self, "reasoning")}')
        return text

    def _collapsed_marker(self) -> Text:
        text = Text()
        text.append(f'{REASONING_MARKER} ', style=color(self, 'reasoning'))
        text.append('Reasoning', style=f'italic {color(self, "reasoning")}')
        text.append(f' · {self._reasoning_size()}', style=color(self, 'muted'))
        text.append('  /reasoning-visibility on', style=color(self, 'muted'))
        return text

    def _reasoning_size(self) -> str:
        """``12 lines``, or ``1 line``. Words, not a token count nobody can verify."""
        count = len(self._thinking.splitlines()) or 1
        return f'{count} line' if count == 1 else f'{count} lines'


class ToolCallBlock(Static):
    """A tool call and its result, drawn as one indented block.

    The header carries the call and a state mark; the body carries the beginning of
    the output, dimmed. The state mark is the only thing that changes while the call
    runs, so a long turn shows which tool is the one that has not returned.
    """

    def __init__(self, name: str, args: Any, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._name = name
        self._args = args
        self._result: Any = None
        self._ok = True
        self._error: str | None = None
        self._pending = True
        self._update_content()

    @property
    def name(self) -> str:
        return self._name

    def set_result(self, output: Any, *, ok: bool = True) -> None:
        self._result = output
        self._ok = ok
        self._pending = False
        self._update_content()

    def set_error(self, message: str) -> None:
        self._error = message
        self._ok = False
        self._pending = False
        self._update_content()

    def repaint(self) -> None:
        """Rebuilds the block after a theme change; see `ChatLog.repaint`."""
        self._update_content()

    def _update_content(self) -> None:
        self.update(self._build())

    def _build(self):
        lines: list[Text] = [self._header()]
        lines.extend(self._body())
        return Group(*lines)

    def _header(self) -> Text:
        border = color(self, 'tool-border')
        text = Text()
        text.append(GUTTER, style=border)
        text.append(f'{self._name}', style=f'bold {color(self, "tool-title")}')
        arguments = format_tool_args(self._args)
        if arguments:
            text.append(f' {arguments}', style=color(self, 'muted'))
        text.append(f'  {self._state_mark()}', style=border)
        return text

    def _state_mark(self) -> str:
        """`…` while running, `✓` or `✗` after. One column, so nothing shifts."""
        if self._pending:
            return '…'
        return '✓' if self._ok else '✗'

    def _body(self) -> list[Text]:
        if self._error:
            return [self._line(self._error, color(self, 'diff-removed'))]
        if self._result is None:
            return []

        shown, omitted = truncate_output(output_lines(self._result))
        if not shown:
            return []
        lines = [self._line(line, color(self, 'tool-output')) for line in shown]
        if omitted:
            lines.append(self._line(f'… +{omitted} more', color(self, 'muted')))
        return lines

    def _line(self, content: str, style: str) -> Text:
        text = Text()
        text.append(GUTTER, style=color(self, 'tool-border'))
        text.append(content, style=style)
        return text


class ChatLog(VerticalScroll):
    """Chat history with auto-scroll anchored to the bottom.

    It uses Textual's anchoring API (`anchor`): while the widget is anchored,
    the compositor keeps the view at the bottom on every layout, so the
    growing content (including streaming with a deferred flush) is followed
    automatically, with no manual scrolls and no timing issues.

    When scrolling up the anchor is released and it is restored only when the
    user goes back to the bottom, so whoever is reading history is respected.
    """

    def __init__(self, *, reasoning_visible: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._current: AssistantBlock | None = None
        self._open_tool: ToolCallBlock | None = None
        self._last_block: Static | None = None
        self._reasoning_visible = reasoning_visible

    def on_mount(self) -> None:
        # Anchors the view to the bottom from the start.
        self.anchor()

    # --- reasoning visibility ----------------------------------------------

    @property
    def reasoning_visible(self) -> bool:
        return self._reasoning_visible

    def set_reasoning_visible(self, visible: bool) -> None:
        """Applies the preference to every block, including the one streaming.

        It rewrites the existing blocks instead of re-rendering the history: the
        conversation on screen is the source of truth, and rebuilding it from the
        session file would lose the pending deltas of a turn in flight.
        """
        self._reasoning_visible = visible
        for child in self.children:
            if isinstance(child, AssistantBlock):
                child.set_reasoning_visible(visible)

    # --- appending ----------------------------------------------------------

    async def _append(self, widget: Static) -> None:
        # Anchoring takes care of the scroll; there is no need to force scroll_end.
        self._last_block = widget
        await self.mount(widget)

    async def add_user(self, text: str, *, queued: bool = False, attachments: Sequence[ImageAttachment] = ()) -> Static:
        """One user message. `queued` marks one the running turn has not read yet."""
        self._current = None
        self._open_tool = None
        widget = Static(_user_renderable(text, queued=queued, attachments=attachments), classes='user-msg')
        if queued:
            # Kept so the line can be repainted when the agent picks it up.
            widget._queued_text = text  # type: ignore[attr-defined]
        # Taken as a tuple and kept private: the block repaints itself when the
        # queue mark goes away, and it has to remember what it was painting.
        widget._attachments = tuple(attachments)  # type: ignore[attr-defined]
        await self._append(widget)
        return widget

    def mark_delivered(self, widget: Static) -> None:
        """Drops the "queued" mark from a message the run just injected."""
        text = getattr(widget, '_queued_text', None)
        if text is None:
            return
        del widget._queued_text  # type: ignore[attr-defined]
        attachments = getattr(widget, '_attachments', ())
        widget.update(_user_renderable(text, queued=False, attachments=attachments))

    async def add_notice(self, text: str, *, style: str = 'yellow') -> None:
        self._current = None
        self._open_tool = None
        await self._append(Static(Text(text, style=style), classes='notice'))

    async def add_tool_call(self, name: str, args: Any) -> None:
        """Opens a tool block. Its result lands in it, not on a line of its own.

        A block that follows another block gets no top margin: a run of calls is one
        indented column, and a blank line between each would break the grouping that
        the gutter is there to create.
        """
        self._current = None
        follows_block = isinstance(self._last_block, ToolCallBlock)
        block = ToolCallBlock(name, args, classes='tool-call tight' if follows_block else 'tool-call')
        self._open_tool = block
        self._last_block = block
        await self._append(block)

    async def add_tool_result(self, name: str, output: Any, *, ok: bool = True) -> None:
        """Closes the open block, or starts one when the call was not announced.

        A result can arrive without a `start` event: a tool that fails before its
        wrapper runs, or a history replayed from a session saved before this block
        existed. Drawing a block with only the result keeps the transcript whole.
        """
        block = self._open_tool
        if block is None or block.name != name:
            block = ToolCallBlock(name, {}, classes='tool-call')
            await self._append(block)
            self._last_block = block
        block.set_result(output, ok=ok)
        self._open_tool = None

    async def add_tool_error(self, name: str, message: str) -> None:
        """Paints a failed call: the tool ran and raised, which is not a tool result."""
        self._current = None
        block = self._open_tool if self._open_tool is not None and self._open_tool.name == name else None
        if block is None:
            block = ToolCallBlock(name, {}, classes='tool-call')
            await self._append(block)
            self._last_block = block
        block.set_error(message)
        self._open_tool = None

    async def begin_response(self) -> None:
        """Marks the start of a new model response."""
        self._current = None
        self._open_tool = None

    async def _ensure_block(self) -> None:
        if self._current is None:
            self._current = AssistantBlock(
                reasoning_visible=self._reasoning_visible,
                classes='assistant-msg',
            )
            await self.mount(self._current)
            self._last_block = self._current

    async def add_thinking(self, delta: str) -> None:
        await self._ensure_block()
        assert self._current is not None
        self._current.add_thinking(delta)

    async def add_text(self, delta: str) -> None:
        await self._ensure_block()
        assert self._current is not None
        self._current.add_text(delta)

    def finalize(self) -> None:
        """Forces the pending render of every block (end of turn/history)."""
        for child in self.children:
            if isinstance(child, AssistantBlock):
                child.finalize()

    def repaint(self) -> None:
        """Rebuilds every block so the text picks up the colors of the new theme.

        A `Static` caches the Rich renderable it was given, so the box would change
        color on a theme switch and the text inside it would keep the old palette.
        Asking each block to rebuild from its own state is what makes the live
        preview in the theme picker real.
        """
        for child in self.children:
            if isinstance(child, AssistantBlock):
                child.finalize()
            elif isinstance(child, ToolCallBlock):
                child.repaint()

    async def clear(self) -> None:
        self._current = None
        self._open_tool = None
        await self.remove_children()
        # After clearing, the new history follows the bottom again.
        self.anchor()

    async def render_history(self, messages: list[ModelMessage]) -> None:
        """Rebuilds the history from the pydantic-ai messages.

        Tool calls and their returns come in separate messages, so the return is
        matched to the block still open — the same path a live turn takes.

        A user prompt that carried images is rebuilt from its content parts: the
        marker is already in the text, and the images come back as the summary the
        live turn printed, so a reloaded conversation reads like the one that was
        typed instead of like a message with a hole in it.

        The widgets are built first and mounted in a single batch. Mounting one at a
        time lets Textual lay out after each, and a layout walks the whole log: that
        made restoring a long conversation quadratic, several seconds for a session
        of a thousand messages, before the window was usable.
        """
        batches: list[Static] = []
        # A tool block receives its result after it is mounted, which is what
        # `add_tool_result` does through `_open_tool`.
        open_tool: ToolCallBlock | None = None
        current: AssistantBlock | None = None

        def follows_a_tool_block() -> bool:
            # The same rule a live turn applies: the margin keys off the block that
            # was appended last, not off the one still waiting for its result. A
            # result clears `open_tool`, and the next call still belongs to the run.
            return bool(batches) and isinstance(batches[-1], ToolCallBlock)

        def block_for_tool(name: str, args: Any) -> ToolCallBlock:
            nonlocal open_tool
            block = ToolCallBlock(name, args, classes='tool-call tight' if follows_a_tool_block() else 'tool-call')
            batches.append(block)
            open_tool = block
            return block

        for message in messages:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, UserPromptPart):
                        current = None
                        open_tool = None
                        batches.append(_user_widget(part.content))
                    elif isinstance(part, ToolReturnPart):
                        ok = getattr(part, 'outcome', 'success') == 'success'
                        block = open_tool if open_tool is not None and open_tool.name == part.tool_name else None
                        if block is None:
                            block = block_for_tool(part.tool_name, {})
                        block.set_result(part.content, ok=ok)
                        open_tool = None
            elif isinstance(message, ModelResponse):
                for part in message.parts:
                    if isinstance(part, ThinkingPart) and part.content:
                        if current is None:
                            current = AssistantBlock(reasoning_visible=self._reasoning_visible, classes='assistant-msg')
                            batches.append(current)
                        current.add_thinking(part.content)
                    elif isinstance(part, TextPart) and part.content:
                        if current is None:
                            current = AssistantBlock(reasoning_visible=self._reasoning_visible, classes='assistant-msg')
                            batches.append(current)
                        current.add_text(part.content)
                    elif isinstance(part, ToolCallPart):
                        current = None
                        block_for_tool(part.tool_name, part.args)
                current = None

        if not batches:
            return
        if self.is_mounted and self.app is not None:
            # One layout for the whole history instead of one per message.
            with self.app.batch_update():
                await self.mount_all(batches)
        else:
            for widget in batches:
                await self.mount(widget)
        self._current = None
        self._open_tool = None
        self._last_block = batches[-1]
