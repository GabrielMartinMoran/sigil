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

import json
import re
import time
from collections.abc import Sequence
from typing import Any, ClassVar

from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.markdown import Markdown
from rich.panel import Panel
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.color import Color
from textual.events import Click
from textual.selection import Selection
from textual.timer import Timer
from textual.widgets import Static

from src.engine.agents.tree.models.agent_node import AgentNode
from src.engine.attachments.content import describe_attachments, images_in
from src.engine.attachments.image_attachment import ImageAttachment
from src.engine.context.images import image_size
from src.engine.tooling.outputs import present_output
from src.engine.tooling.presentation import ToolSummary, display_name
from src.ui.markdown_theme import markdown_themes
from src.ui.messages import NodeOpened, RollbackRequested
from src.ui.palette import color
from src.ui.subagent_reports import split_reports
from src.ui.widgets.empty_logo import logo_lines
from src.ui.widgets.reading import ReadOnlyScroll
from src.ui.widgets.rows import fit
from src.ui.widgets.status_bar import SPINNER_FRAMES, SPINNER_INTERVAL

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

FAILURE_LINES = 3
"""Lines of output a failed call shows while collapsed.

The first line of a failure is usually the whole story (`FAILED test_x - NameError`),
the second and third give it context, and past that it is a stack trace nobody reads
in a transcript. More is a `/tools expanded` away.
"""

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


def format_duration(duration_ms: float | None) -> str:
    """How long a call took, in one unit and one precision.

    Milliseconds below a second — that is where tools live, and `450ms` reads faster
    than `0.45s` — and seconds with a decimal above it. No third unit: a turn is not
    going to have a tool that takes an hour, and if it does, `3700.0s` says it.
    """
    if duration_ms is None:
        return ''
    if duration_ms < 1000:
        return f'{duration_ms:.0f}ms'
    return f'{duration_ms / 1000:.1f}s'


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

    Two shapes, because the tools have two: a result that carries a process's output
    shows that output, and anything else shows its text. `str` on a `RunToolOutput`
    gives `returncode=0 stdout='...' stderr=None`: a repr that repeats in one long line
    what the row already summarized, and hides the output a failure is about.

    Leading whitespace is kept: a diff, a stack trace or a snippet of code only reads
    correctly with its indentation, and a tool that answers with code is the common
    case. Trailing whitespace is dropped — it is invisible and costs width.
    """
    text = value if isinstance(value, str) else present_output('', value).text
    lines = [line.rstrip() for line in text.splitlines()]
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
                image_id=part.identifier,
            )
        )
    return tuple(described)


class SubagentReport(Static):
    """A compact, clickable record of a report delivered to the parent."""

    def __init__(self, node: AgentNode) -> None:
        super().__init__(Text(f'↩ {node.label} #{node.short_id} sent a report.', style='cyan'))
        self.node_id = node.id
        self.add_class('clickable')

    def on_click(self) -> None:
        self.post_message(NodeOpened(self.node_id))


class AgentMessageBlock(Static):
    """An instruction delivered by another agent, not a human message."""


class UserMessage(Static):
    """One user message, clickable when its place in the conversation is known.

    A click does not decide anything: it opens the screen that asks what to do with
    the instant *before* this message — roll back to it, or fork from it. Deciding
    here would be deciding for the user on the one gesture that can destroy a turn.

    ``seq`` is the position of the prompt in the root transcript, which is what maps
    it to its turn. It is ``None`` for a message that has not reached a checkpoint yet,
    and the click is honest about that: it does nothing instead of guessing.
    """

    def __init__(self, renderable: Any, *, seq: int | None = None, **kwargs: Any) -> None:
        super().__init__(renderable, **kwargs)
        self.seq = seq

    def on_click(self) -> None:
        if self.seq is None:
            return
        self.post_message(RollbackRequested(self.seq))


def _user_widget(content: str | list[Any], *, seq: int | None = None, from_agent: bool = False) -> Static:
    """One user line, from a plain prompt or from text plus images.

    Both shapes reach `render_history`: a turn typed before images existed saved
    its prompt as a string, and one with a pasted screenshot saved it as a list of
    text and `BinaryContent`. The marker `[Image #n]` is already in the text, so
    the images only have to be summarised back into the line under it.
    """
    if isinstance(content, str):
        text, described = content, ()
    else:
        text = ' '.join(item for item in content if isinstance(item, str))
        described = _describe_parts(images_in(content))
    rendered = _user_renderable(text, queued=False, attachments=described)
    if from_agent:
        instruction = Text('↳ Agent: ', style='bold')
        instruction.append_text(rendered[2:])
        return AgentMessageBlock(instruction, classes='agent-msg')
    return UserMessage(rendered, seq=seq, classes='user-msg')


_URL = re.compile(r'https?://[^\s<>\[\]()]+')


def link_urls(text: Text) -> Text:
    for match in _URL.finditer(text.plain):
        url = match.group().rstrip('.,;:!?')
        text.stylize(Style(link=url, underline=True), match.start(), match.start() + len(url))
    return text


class LinkedMarkdown:
    """Render Markdown while adding terminal links to bare HTTP URLs."""

    def __init__(self, text: str, widget: Any = None) -> None:
        self.theme, code_theme = markdown_themes(lambda name: color(widget, name))
        self.markdown = Markdown(text, code_theme=code_theme)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        with console.use_theme(self.theme):
            yield from self._render_linked(console, options)

    def _render_linked(self, console: Console, options: ConsoleOptions) -> RenderResult:
        for segment in console.render(self.markdown, options):
            if segment.control or (segment.style and segment.style.link):
                yield segment
                continue
            start = 0
            for match in _URL.finditer(segment.text):
                url = match.group().rstrip('.,;:!?')
                yield Segment(segment.text[start : match.start()], segment.style)
                yield Segment(url, (segment.style or Style()) + Style(link=url, underline=True))
                start = match.start() + len(url)
            yield Segment(segment.text[start:], segment.style)


class LinkNotice(Static):
    def on_click(self, event: Click) -> None:
        url = event.style.link if event.style else None
        if url and url.startswith(('https://', 'http://')):
            self.app.open_url(url)
            event.stop()


class AssistantBlock(Static):
    """One model response block: collapsed reasoning plus the Markdown answer.

    The Markdown is re-rendered at most every `RENDER_INTERVAL` seconds so as not
    to re-parse it on every delta; a final flush is guaranteed with `finalize()`.
    """

    def __init__(self, *, reasoning_visible: bool = False, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._thinking = ''
        self._has_thinking = False
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
        self._has_thinking = True
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

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        console = self.app.console
        options = console.options.update_width(max(1, self.content_size.width))
        rendered = ''.join(segment.text for segment in console.render(self._build(), options) if not segment.control)
        return selection.extract(rendered), '\n'

    def on_click(self, event: Click) -> None:
        url = event.style.link if event.style else None
        if url and url.startswith(('https://', 'http://')):
            self.app.open_url(url)
            event.stop()

    # --- rendering ----------------------------------------------------------

    def _build(self):
        parts: list[object] = []
        if self._has_thinking:
            parts.append(self._reasoning_renderable())
        if self._text:
            parts.append(LinkedMarkdown(self._text, self))
        if not parts:
            return Text('')
        if len(parts) == 1:
            return parts[0]
        return Group(*parts)

    def _reasoning_renderable(self):
        """The reasoning, expanded or as a one-line marker.

        The marker keeps the presence and size of hidden reasoning visible without
        repeating command help throughout the transcript.
        """
        if not self._reasoning_visible:
            return self._collapsed_marker()

        text = Text()
        text.append(f'{REASONING_MARKER} ', style=color(self, 'reasoning'))
        text.append('Reasoning\n', style=f'italic {color(self, "reasoning")}')
        for line in (self._thinking or 'unavailable').splitlines():
            text.append(INDENT, style=color(self, 'reasoning'))
            text.append(f'{line}\n', style=f'italic {color(self, "reasoning")}')
        return text

    def _collapsed_marker(self) -> Text:
        text = Text()
        text.append(f'{REASONING_MARKER} ', style=color(self, 'reasoning'))
        text.append('Reasoning', style=f'italic {color(self, "reasoning")}')
        if self._thinking:
            text.append(f' · {self._reasoning_size()}', style=color(self, 'muted'))
        return text

    def _reasoning_size(self) -> str:
        """``12 lines``, or ``1 line``. Words, not a token count nobody can verify."""
        count = len(self._thinking.splitlines()) or 1
        return f'{count} line' if count == 1 else f'{count} lines'


class ToolCallBlock(Static):
    """A tool call and its result, drawn as one indented block.

    The header carries the call and, flush right, what came of it: how long it took
    and the one line the tool chose to describe its result (`9 passed in 0.45s`,
    `3 replacements`). That summary comes from the tool itself — see
    `engine.tooling.presentation` — and not from `str(result)`, which is how a
    Pydantic repr used to end up in the middle of a conversation.

    The glyph and color summarize the user-visible outcome: `✓` for a completed
    call, `✗` for a raised call or a declared bad result. The detail distinguishes
    a failed test from a tool exception; both deserve a visible failure mark.

    The body carries the beginning of the output, dimmed.
    """

    can_focus = True
    BINDINGS: ClassVar = [('enter', 'toggle_output', 'Tool output'), ('space', 'toggle_output', 'Tool output')]

    #: Fields the subclass needs before the first paint.
    _pending_state: bool = True

    def __init__(self, name: str, args: Any, expanded: bool = False, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._name = name
        self._args = args
        self._expanded = expanded
        self._result: Any = None
        self._ok = True
        self._error: str | None = None
        self._pending = True
        self._pending_frame = 0
        self._pending_started = time.monotonic()
        self._pending_timer: Timer | None = None
        self._summary: ToolSummary | None = None
        self._duration_ms: float | None = None
        self._prepare()
        self._update_content()

    def _prepare(self) -> None:
        """Hook for a subclass that needs its own state before the first paint.

        `__init__` paints once at the end, and a subclass that set its fields after
        `super().__init__` would be painted with none of them.
        """

    def on_mount(self) -> None:
        if self._pending:
            self._pending_timer = self.set_interval(SPINNER_INTERVAL, self._advance_pending)

    def on_unmount(self) -> None:
        self._stop_pending_timer()

    def _advance_pending(self) -> None:
        if self._pending:
            self._pending_frame = (self._pending_frame + 1) % len(SPINNER_FRAMES)
            self._update_content()

    def _stop_pending_timer(self) -> None:
        if self._pending_timer is not None:
            self._pending_timer.stop()
            self._pending_timer = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def pending(self) -> bool:
        """Whether the call has not produced an outcome yet."""
        return self._pending

    @property
    def raised(self) -> bool:
        """Whether the call itself failed, as opposed to returning a bad result."""
        return self._error is not None

    @property
    def duration_ms(self) -> float | None:
        return self._duration_ms

    def set_result(self, output: Any, *, ok: bool = True) -> None:
        shown = present_output(self._name, output)
        self._result = shown.text
        if self._summary is None:
            self._summary = shown.summary
        self._ok = ok
        self._pending = False
        self._stop_pending_timer()
        self._update_content()

    def set_error(self, message: str) -> None:
        self._error = message
        self._ok = False
        self._pending = False
        self._stop_pending_timer()
        self._update_content()

    def set_summary(self, summary: ToolSummary | None, duration_ms: float | None = None) -> None:
        """Records what the tool said about itself, and how long it took.

        It arrives from the tool event, which is emitted when the tool returns — before
        the run streams the result back. The block can therefore say "3 replacements,
        120 ms" while the output is still on its way to the body.
        """
        self._summary = summary
        self._duration_ms = duration_ms
        self._update_content()

    def set_expanded(self, expanded: bool) -> None:
        """Shows or hides the output of the call."""
        self._expanded = expanded
        self._update_content()

    def action_toggle_output(self) -> None:
        self.set_expanded(not self._expanded)

    def on_click(self, event: Click) -> None:
        event.stop()
        url = event.style.link if event.style else None
        if url and url.startswith(('https://', 'http://')):
            self.app.open_url(url)
            return
        self.action_toggle_output()

    @property
    def failed(self) -> bool:
        return not self._ok or self._failed

    def repaint(self) -> None:
        """Rebuilds the block after a theme change; see `ChatLog.repaint`."""
        self._update_content()

    def on_resize(self) -> None:
        """Rebuilds the header: the right column is aligned to the current width."""
        self._update_content()

    def _update_content(self) -> None:
        self.update(self._build())

    def _build(self):
        body = [link_urls(line) for line in self._body()]
        if self._expanded and body:
            output = Text('\n').join(line[len(GUTTER) :] for line in body)
            if self._name == 'run':
                command = Text(f'$ {self._argument_hint()}\n', style=color(self, 'tool-title'))
                output = command + output
            panel = Panel(output, border_style=color(self, 'tool-border'), padding=(0, 1))
            return Group(self._header(), panel)
        return Group(self._header(), *body)

    def _header(self) -> Text:
        """The call on the left, the outcome flush right, on the block's own width.

        The state mark goes **first**, right after the gutter, and not at the end of the
        call. At the end it was the first thing a narrow row clipped — the row that most
        needed to say "this one failed" was the one that lost its mark. Leading it also
        matches how the call reads: the outcome of the call is what is being scanned.
        """
        border = color(self, 'tool-border')
        text = Text()
        text.append(GUTTER, style=border)
        state_style = color(self, 'error' if self.failed else 'success') if not self._pending else border
        text.append(f'{self._state_mark()} ', style=state_style)
        name = display_name(self._name)
        text.append(name.ljust(12), style=color(self, 'tool-title'))
        arguments = self._argument_hint()
        if arguments:
            text.append(f' {arguments}', style=color(self, 'text'))
        return fit(text, self._outcome(), self.size.width)

    def _argument_hint(self) -> str:
        args = self._args
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                return preview(args)
        if isinstance(args, dict):
            for key in ('cmds', 'command', 'path', 'query', 'url'):
                value = args.get(key)
                if isinstance(value, list):
                    return preview(' ; '.join(str(item) for item in value))
                if isinstance(value, str) and value:
                    return preview(value)
        return format_tool_args(args)

    def _outcome(self) -> Text | None:
        """`9 passed in 0.45s · 1.2s`: the tool's summary, then how long it took.

        While running, show a live elapsed time instead of an outcome. After a
        reload, no duration is invented: it lives in the event, not in storage.
        """
        if self._pending:
            elapsed = max(0, int(time.monotonic() - self._pending_started))
            return Text(f'running · {elapsed}s', style=color(self, 'muted'))
        parts: list[tuple[str, str]] = []
        if self._summary is not None:
            parts.append((self._summary.text, self._status_style(self._summary.status)))
        elapsed = format_duration(self._duration_ms)
        if elapsed:
            parts.append((elapsed, color(self, 'muted')))
        if self._result is not None or self._error:
            parts.append(('⌄' if self._expanded else '›', color(self, 'muted')))
        if not parts:
            return None
        text = Text(no_wrap=True, overflow='ellipsis')
        for index, (content, style) in enumerate(parts):
            if index:
                text.append(' · ', style=color(self, 'muted'))
            text.append(content, style=style)
        return text

    def _status_style(self, status: str) -> str:
        """A bad result is red, a good one green, an unknown one muted.

        The summary preserves the declared status; the glyph also marks a bad
        result as failed, without turning it into a tool exception.
        """
        return {'good': color(self, 'success'), 'bad': color(self, 'error')}.get(status, color(self, 'muted'))

    def _state_mark(self) -> str:
        """An animated mark while running, `✓` or `✗` after; always one cell."""
        if self._pending:
            return SPINNER_FRAMES[self._pending_frame]
        return '✗' if self.failed else '✓'

    @property
    def _failed(self) -> bool:
        """Whether the tool reported a bad result, as opposed to raising."""
        return self._summary is not None and self._summary.status == 'bad'

    def _body(self) -> list[Text]:
        """The output: expanded, or collapsed to one line.

        Collapsed by default, and **a failure is never fully collapsed**. A tool that
        raised shows its error; a call whose summary came back `bad` shows the first
        lines that explain it. Hiding a failure behind a preference nobody has set yet
        is hiding the one thing the reader has to act on, so the exception lives here
        and not in the preference.
        """
        if self._error:
            return [self._line(f'↳ {self._error}', color(self, 'diff-removed'))]
        if self._result is None:
            return []

        shown, omitted = truncate_output(output_lines(self._result))
        if not shown:
            return []
        total = len(shown) + omitted
        if self._expanded:
            lines = [self._line(line, color(self, 'tool-output')) for line in shown]
            if omitted:
                lines.append(self._line(f'… +{omitted} more', color(self, 'muted')))
            return lines
        if self._failed:
            lines = [self._line(f'↳ {line}', color(self, 'diff-removed')) for line in shown[:FAILURE_LINES]]
            if total > FAILURE_LINES:
                lines.append(self._line(f'… +{total - FAILURE_LINES} more', color(self, 'muted')))
            return lines
        if self._summary is not None:
            # The row already says what came back. A `… 12 lines` under a row that
            # reads `9 passed in 0.45s` is a second line for nothing.
            return []
        # No presenter and no preference to expand: say that there *is* output, so the
        # row is not a ✓ with nothing behind it.
        return [self._line(f'… {total} line' + ('s' if total != 1 else ''), color(self, 'muted'))]

    def _line(self, content: str, style: str) -> Text:
        text = Text()
        text.append(GUTTER, style=color(self, 'tool-border'))
        text.append(content, style=style)
        return text


MIN_GROUP_ROWS = 2
"""Rows a batch needs before its header is worth drawing."""


class ToolGroupHeader(Static):
    """The one line above a batch of calls: `3 tools [2/3]`, then `3 tools · 1 error`.

    A model that asks for three tools in one response produces three rows. On their own
    they read as three unrelated things that happened to arrive together; the header is
    what says they are one step, and how far along it is.

    It is a header and **not a container**. The rows stay direct children of the
    transcript, which is what keeps them independently paintable, clickable (a `summon`
    opens its subagent) and testable. The bar down the left of the group is each row's
    own gutter, already there; a container would have to redraw it and indent
    everything.

    It mutates instead of having a footer. A header *and* a footer would be two lines of
    chrome for one batch, and they can disagree — the mockup that inspired this had
    `1 failed` in one and `all passed` in the other.

    It is hidden while the batch has a single call: one call needs no grouping, and the
    row says everything a header would.
    """

    can_focus = True
    BINDINGS: ClassVar = [('enter', 'toggle_group', 'Tool group'), ('space', 'toggle_group', 'Tool group')]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._rows: list[ToolCallBlock] = []
        self._collapsed = False

    def action_toggle_group(self) -> None:
        if self.closed:
            self._collapsed = not self._collapsed
            self.refresh_from(self._rows)

    def on_click(self, event: Click) -> None:
        event.stop()
        self.action_toggle_group()

    def refresh_from(self, rows: Sequence[ToolCallBlock]) -> None:
        """Repaints from the rows the transcript is holding for this batch."""
        self._rows = list(rows)
        self.display = len(self._rows) >= MIN_GROUP_ROWS
        for index, row in enumerate(self._rows):
            row.set_class(self.display or index > 0, 'tight')
            row.display = not self._collapsed or row.failed or row.pending
        self.update(self._build())

    def on_resize(self) -> None:
        self.update(self._build())

    @property
    def closed(self) -> bool:
        """Whether every row has an outcome: the batch is over."""
        return all(not row.pending for row in self._rows)

    def _build(self) -> Text:
        total = len(self._rows)
        done = sum(not row.pending for row in self._rows)
        errors = sum(row.raised for row in self._rows)
        failed = sum(row.failed and not row.raised for row in self._rows)
        text = Text(GUTTER, style=color(self, 'tool-border'))
        mark = '⋮ ' if not self.closed else '✗ ' if errors or failed else '✓ '
        text.append(mark, style=color(self, 'error' if errors or failed else 'tool-title'))
        text.append(f'{total} tools' + (' completed' if self.closed else ''), style=f'bold {color(self, "tool-title")}')
        badge = self._badge(done, total, errors, failed)
        text.append(f'  {badge}', style=color(self, 'tool-title'))
        elapsed = format_duration(self._longest()) if self.closed else ''
        right = Text(f'{elapsed}  {"›" if self._collapsed else "⌄"}', style=color(self, 'muted'))
        header = fit(text, right, self.size.width)
        if self._collapsed:
            counts: dict[str, int] = {}
            for row in self._rows:
                name = display_name(row.name)
                counts[name] = counts.get(name, 0) + 1
            detail = ' · '.join(f'{count} {name}' for name, count in counts.items())
            header.append(f'\n{GUTTER}  {detail} · collapsed group', style=color(self, 'muted'))
        return header

    def _badge(self, done: int, total: int, errors: int, failed: int) -> str:
        if not self.closed:
            return f'{done} / {total} done'
        if errors or failed:
            return ' · '.join(
                part for part in (f'{errors} error' if errors else '', f'{failed} failed' if failed else '') if part
            )
        if self._rows and all(row._summary is not None and row._summary.status == 'good' for row in self._rows):
            return 'all passed'
        return 'done'

    def _longest(self) -> float | None:
        durations = [row.duration_ms for row in self._rows if row.duration_ms is not None]
        return max(durations) if durations else None


class SummonBlock(ToolCallBlock):
    """A subagent being started, drawn as a hand-off and not as a tool call.

    `summon` is a tool, and for a moment it looked like one: a name, a JSON blob and
    a state mark. But what the user is watching is not a call — it is another agent
    beginning to work, and the id in the result is the handle they will use for the
    rest of the session. So the block reads as a spawn:

        ⠋ Scout
          read the tree end to end and report the call path…
          async · #1a2b3c · working

    The state comes from the result text, which is what a replayed transcript has:
    the block has to be rebuildable from the history alone, with no side channel.
    """

    def _prepare(self) -> None:
        """Reads the call before the base class paints for the first time."""
        self._spawn = self._read_args(self._args)
        self._status = 'starting'
        self._node_id = ''
        self._node_name = ''
        self._clickable = False
        self._background_running = False

    def on_mount(self) -> None:
        if self._pending or self._background_running:
            self._pending_timer = self.set_interval(SPINNER_INTERVAL, self._advance_pending)

    def _advance_pending(self) -> None:
        if self._pending or self._background_running:
            self._pending_frame = (self._pending_frame + 1) % len(SPINNER_FRAMES)
            self._update_content()

    def set_node(self, node: AgentNode) -> None:
        """Reconcile a background hand-off with its actual execution."""
        if not self._node_id or not node.id.startswith(self._node_id):
            return
        # A resume carries no profile in its arguments, so the node it continues is
        # where its name comes from. Without it the block would go on naming an id,
        # which says nothing about who is actually working.
        renamed = False
        if not self._spawn['agent'] and not self._node_name:
            self._node_name = node.name
            renamed = True
        if self._status != 'working':
            if renamed:
                self._update_content()
            return
        self._background_running = not node.status.finished
        if self._background_running:
            if self.is_mounted and self._pending_timer is None:
                self._pending_timer = self.set_interval(SPINNER_INTERVAL, self._advance_pending)
        else:
            self._stop_pending_timer()
            self._status = {
                'done': 'out of steps' if node.budget_exhausted else 'finished',
                'failed': 'failed',
                'cancelled': 'stopped',
            }[node.status.value]
        self._update_content()

    def on_click(self) -> None:
        """A click opens the subagent, once the block knows which one it is.

        Before the result lands there is no id to open, and a click that silently
        does nothing is better than one that opens whatever was focused: the block
        is still a line of text at that point.
        """
        if not self._node_id:
            return
        self.post_message(NodeOpened(self._node_id))

    @staticmethod
    def _read_args(args: Any) -> dict[str, str]:
        """The four fields of a spawn, from the call arguments.

        A live turn hands them over as a ``dict``; a history read back from disk
        hands them over as a JSON **string**, because that is how the message was
        stored. Both are the same call, so both are understood — a block that lost
        the name the moment the session was reopened is a block that lied about what
        the user was looking at.
        """
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = None
        if not isinstance(args, dict):
            return {'agent': '', 'task': '', 'mode': 'sync', 'agent_id': ''}
        return {
            'agent': str(args.get('agent') or ''),
            'task': str(args.get('task') or ''),
            'mode': str(args.get('mode') or 'sync'),
            'agent_id': str(args.get('agent_id') or '').lstrip('#'),
        }

    def set_result(self, output: Any, *, ok: bool = True) -> None:
        super().set_result(output, ok=ok)
        text = str(output)
        found = re.search(r'#([0-9a-f]{8,})', text)
        if found:
            self._node_id = found.group(1)
            # From here the block is a way in: it paints itself as one, so the
            # hover is the only hint a reader needs that it can be clicked.
            self._clickable = True
            self.add_class('clickable')
        # A resume names no profile in its arguments; its answer does, in the
        # envelope: `[subagent Scout (scout #1a2b3c, sync) finished]`. An async one
        # answers `#1a2b3c (Scout) is working in the background`, so both shapes are
        # read here: a replayed block has nothing but this text to name itself from.
        named = re.search(r'\(([a-z0-9][a-z0-9-]*)\s+#', text) or re.match(r'#[0-9a-f]{8,}\s+\(([^)]+)\)', text)
        if named:
            self._node_name = named.group(1)
        self._status = self._read_status(text, ok=ok)
        self._update_content()

    def set_error(self, message: str) -> None:
        super().set_error(message)
        self._status = 'refused'
        self._update_content()

    @staticmethod
    def _read_status(text: str, *, ok: bool) -> str:
        """What the result says happened, in the words of the block."""
        lowered = text.lower()
        if not ok:
            return 'refused'
        if 'out of steps' in lowered:
            return 'out of steps'
        if 'was stopped' in lowered or 'stopped, along' in lowered:
            return 'stopped'
        if (
            'is working in the background' in lowered
            or 'working on it now' in lowered
            or 'moved to the background' in lowered
        ):
            return 'working'
        if 'finished' in lowered:
            return 'finished'
        if 'already finished' in lowered or 'had already' in lowered:
            return 'finished'
        return 'started'

    def _build(self) -> Group:
        lines: list[Text] = [self._spawn_header()]
        detail = self._detail_line()
        if detail is not None:
            lines.append(detail)
        lines.append(self._meta_line())
        return Group(*lines)

    def _spawn_header(self) -> Text:
        border = color(self, 'tool-border')
        resumed = bool(self._spawn['agent_id'])
        text = Text()
        text.append(GUTTER, style=border)
        mark, mark_style = self._state_mark()
        text.append(f'{mark} ', style=mark_style)
        # One name, and never the word "subagent" standing in for one: the label is
        # the profile name with its dashes opened up, so painting both would be
        # saying the same thing twice.
        label = self._label()
        if label:
            # Underlined once there is something to open: the name is the target,
            # and the hover confirms it.
            text.append(
                label,
                style=f'bold underline {color(self, "tool-title")}'
                if self._clickable
                else f'bold {color(self, "tool-title")}',
            )
        elif not resumed:
            text.append('(unknown agent)', style=color(self, 'muted'))
        if resumed:
            # The id is what a resume runs on, so it goes next to the name it
            # continues: `Pas Developer (#f7c01c8a)` says which execution this is,
            # where the bare id said nothing about who was working. A spawn never
            # carries one, which is what makes the parenthesis the resume mark.
            short = f'#{self._spawn["agent_id"][:8]}'
            text.append(f' ({short})' if label else f'  resume {short}', style=color(self, 'muted'))
        return text

    def _state_mark(self) -> tuple[str, str]:
        """The mark of the hand-off: animated while it works, `✓` once it is done.

        A `●` used to stand for every outcome, and it is the mark of a node that is
        *running* everywhere else in the UI (the tree, the node header). A finished
        subagent is a completed step, so it is painted as one — and a failure or a
        stop keeps its own mark instead of looking like a hand-off still in flight.
        """
        if self._pending or self._background_running:
            return SPINNER_FRAMES[self._pending_frame], f'bold {color(self, "success")}'
        return {
            'finished': ('✓', color(self, 'success')),
            'out of steps': ('✓', 'yellow'),
            'failed': ('✖', color(self, 'diff-removed')),
            'refused': ('✖', color(self, 'diff-removed')),
            'stopped': ('◼', color(self, 'muted')),
        }.get(self._status, ('●', color(self, 'muted')))

    def _label(self) -> str:
        """The agent's name, humanized: from the call, or from its own answer."""
        name = self._spawn['agent'] or self._node_name
        return name.replace('-', ' ').title() if name else ''

    def _detail_line(self) -> Text | None:
        """The task, one line, clipped: a block is not a briefing."""
        task = ' '.join(self._spawn['task'].split())
        if not task:
            return None
        limit = TOOL_OUTPUT_WIDTH
        if len(task) > limit:
            task = task[: limit - 1].rstrip() + '…'
        text = Text()
        text.append(GUTTER, style=color(self, 'tool-border'))
        text.append(task, style=color(self, 'tool-output'))
        return text

    def _meta_line(self) -> Text:
        """Mode, id and state: the three facts the user acts on."""
        border = color(self, 'tool-border')
        text = Text()
        text.append(GUTTER, style=border)
        text.append(self._spawn['mode'], style=color(self, 'muted'))
        # A resume already painted the id beside the name, where it is the mark of
        # the execution being continued; repeating it here would be the same id twice.
        if self._node_id and not self._spawn['agent_id']:
            text.append(' · ', style=color(self, 'muted'))
            text.append(f'#{self._node_id[:8]}', style=f'bold {color(self, "muted")}')
        text.append(' · ', style=color(self, 'muted'))
        text.append(self._status, style=self._status_style())
        return text

    def _status_style(self) -> str:
        style = {
            'working': 'bold yellow',
            'finished': color(self, 'success'),
            'out of steps': 'yellow',
            'refused': color(self, 'diff-removed'),
            'failed': color(self, 'diff-removed'),
            'stopped': color(self, 'muted'),
        }.get(self._status, color(self, 'muted'))
        return style


def block_for(name: str, args: Any, *, classes: str, expanded: bool = False) -> ToolCallBlock:
    """The block a tool call gets. Only `summon` reads as a spawn."""
    if name == 'summon':
        return SummonBlock(name, args, classes=classes, expanded=expanded)
    return ToolCallBlock(name, args, classes=classes, expanded=expanded)


class ChatLog(ReadOnlyScroll):
    """Chat history with auto-scroll anchored to the bottom.

    It is a `ReadOnlyScroll`: the transcript is read, never typed into, so it does
    not take the keyboard (see `src.ui.widgets.reading`). The keys that scroll it
    live in the composer.

    It uses Textual's anchoring API (`anchor`): while the widget is anchored,
    the compositor keeps the view at the bottom on every layout, so the
    growing content (including streaming with a deferred flush) is followed
    automatically, with no manual scrolls and no timing issues.

    When scrolling up the anchor is released and it is restored only when the
    user goes back to the bottom, so whoever is reading history is respected.
    """

    def __init__(
        self,
        *,
        reasoning_visible: bool = False,
        tools_expanded: bool = False,
        welcome: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._tools_expanded = tools_expanded
        self._current: AssistantBlock | None = None
        self._open_tools: dict[str, list[ToolCallBlock]] = {}
        # The header of the batch being drawn, and its rows. `None` between batches.
        self._group: ToolGroupHeader | None = None
        self._group_rows: list[ToolCallBlock] = []
        self._last_block: Static | None = None
        self._reasoning_visible = reasoning_visible
        self._welcome = welcome
        self._empty = True

    # --- the empty transcript ------------------------------------------------

    def render(self) -> Text:
        """The Sigil mark, centered, while nothing has been said yet.

        A container paints its own content behind its children, so the mark needs
        no widget of its own: it is drawn until the first block arrives and then
        it simply stops being drawn. Notices are the exception — a new session
        announces itself and the transcript is still empty.
        """
        if not self._welcome or not self._empty:
            return Text('')
        width, height = self.content_size
        lines = logo_lines(width, max(0, height - 2), *self._logo_colors())
        block = max((len(line.plain) for line in lines), default=0)
        left = ' ' * max(0, (width - block) // 2)
        result = Text('\n' * max(0, (height - len(lines)) // 2), no_wrap=True, overflow='crop')
        for index, line in enumerate(lines):
            if index:
                result.append('\n')
            result.append(left)
            result.append_text(line)
        return result

    def _logo_colors(self) -> tuple[str, str]:
        """Top and bottom of the gradient, from the theme.

        A theme names both ends when it has a gradient in mind; the rest fall back
        to their own accent pair. A theme whose two ends are the same color would
        paint a flat silhouette and lose the depth the mark is drawn for, so it is
        blended toward the foreground by a step that still reads as light.
        """
        variables = getattr(self.app, 'theme_variables', None) or {}
        start = variables.get('logo-start', '') or color(self, 'primary')
        end = variables.get('logo-end', '') or color(self, 'secondary')
        if Color.parse(start) == Color.parse(end):
            base = Color.parse(end)
            target = Color.parse(color(self, 'foreground'))
            if base == target:
                target = Color.parse('#000000' if base.brightness > 0.5 else '#ffffff')
            end = base.blend(target, 0.4).hex
        return start, end

    def _mark_filled(self) -> None:
        """The transcript is no longer empty: the mark goes away."""
        if self._empty:
            self._empty = False
            self.refresh()

    def on_mount(self) -> None:
        # Anchors the view to the bottom from the start.
        self.anchor()

    # --- open calls -----------------------------------------------------------

    def _claim(self, name: str) -> ToolCallBlock | None:
        """Takes the oldest open block for ``name``, or ``None``.

        A dict of lists and not a single slot, because a batch runs in parallel: the
        model asks for three tools, all three emit `start`, and the results come back
        in whatever order they finish. With one slot, the second start overwrote the
        first and the first result — finding its block gone — opened a *new* one with
        no arguments, which is how a batch used to turn into six blocks that could not
        be matched to each other.

        Oldest first: a tool called twice in the same batch gets its results in the
        order it was asked for, which is the order a reader expects.
        """
        blocks = self._open_tools.get(name)
        if not blocks:
            return None
        return blocks.pop(0)

    def _remember(self, block: ToolCallBlock) -> None:
        self._open_tools.setdefault(block.name, []).append(block)
        self._group_rows.append(block)
        if self._group is not None:
            self._group.refresh_from(self._group_rows)

    def _start_group(self) -> None:
        """Opens a new batch header for the next run of calls."""
        self._group_rows = []
        self._group = ToolGroupHeader(classes='tool-group')

    def _close_group(self) -> None:
        """Ends the batch: the header disappears if the batch had a single call.

        It is created with the first call because there is no way to know a second one
        is coming. Rather than guess, it is drawn and then hidden — `MIN_ROWS` decides.
        """
        if self._group is not None:
            self._group.refresh_from(self._group_rows)
        self._group = None
        self._group_rows = []

    def _refresh_group(self) -> None:
        if self._group is not None:
            self._group.refresh_from(self._group_rows)

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

    def set_tools_expanded(self, expanded: bool) -> None:
        """Applies the preference to the calls already on screen and the ones to come."""
        self._tools_expanded = expanded
        for child in self.children:
            if isinstance(child, ToolCallBlock):
                child.set_expanded(expanded)

    # --- appending ----------------------------------------------------------

    async def _append(self, widget: Static) -> None:
        # Anchoring takes care of the scroll; there is no need to force scroll_end.
        self._last_block = widget
        if not isinstance(widget, LinkNotice):
            self._mark_filled()
        await self.mount(widget)

    async def add_user(
        self,
        text: str,
        *,
        queued: bool = False,
        attachments: Sequence[ImageAttachment] = (),
        seq: int | None = None,
    ) -> Static:
        """One user message. `queued` marks one the running turn has not read yet.

        ``seq`` is where the prompt will land in the root transcript. The app knows it
        when it submits — the prompt is the next message — and passes it so the line can
        be clicked before the turn even finishes.
        """
        self._current = None
        self._open_tools.clear()
        self._close_group()
        widget = UserMessage(
            _user_renderable(text, queued=queued, attachments=attachments), seq=seq, classes='user-msg'
        )
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
        self._open_tools.clear()
        self._close_group()
        await self._append(LinkNotice(link_urls(Text(text, style=style)), classes='notice'))

    async def add_tool_call(self, name: str, args: Any) -> None:
        """Opens a tool block. Its result lands in it, not on a line of its own.

        A block that follows another block gets no top margin: a run of calls is one
        indented column, and a blank line between each would break the grouping that
        the gutter is there to create.
        """
        self._current = None
        if self._group is None:
            self._start_group()
            assert self._group is not None
            await self._append(self._group)
        follows_block = isinstance(self._last_block, (ToolCallBlock, ToolGroupHeader))
        block = block_for(
            name,
            args,
            classes='tool-call tight' if follows_block else 'tool-call',
            expanded=self._tools_expanded,
        )
        self._remember(block)
        self._last_block = block
        await self._append(block)

    async def add_tool_result(self, name: str, output: Any, *, ok: bool = True, args: Any = None) -> None:
        """Closes the open block, or starts one when the call was not announced.

        A result can arrive without a `start` event: a tool that fails before its
        wrapper runs, or a history replayed from a session saved before this block
        existed. Drawing a block with only the result keeps the transcript whole.
        """
        block = self._claim(name)
        if block is None:
            block = block_for(name, args or {}, classes='tool-call', expanded=self._tools_expanded)
            await self._append(block)
            self._last_block = block
        block.set_result(output, ok=ok)
        if isinstance(block, SummonBlock) and self.is_mounted:
            tree_nodes = getattr(self.app, 'tree_nodes', None)
            if callable(tree_nodes):
                for node in tree_nodes():
                    block.set_node(node)
        self._refresh_group()

    def set_tool_summary(self, name: str, summary: ToolSummary | None, duration_ms: float | None) -> bool:
        """Adds what the tool said about itself to its open block.

        It arrives from the tool event, which is emitted when the tool returns. The
        block is matched by name and not by identity because the event and the stream
        are two different paths into the same call; an open block that is not this
        tool's is left alone. Returns whether a block took it.
        """
        blocks = self._open_tools.get(name)
        if not blocks:
            return False
        # The summary belongs to the call that just finished, and it is the first one
        # still open: taking it out of the list here would close a block that is still
        # waiting for its own result, so it is peeked at and not claimed.
        blocks[0].set_summary(summary, duration_ms)
        self._refresh_group()
        return True

    async def add_tool_error(self, name: str, message: str) -> None:
        """Paints a failed call: the tool ran and raised, which is not a tool result."""
        self._current = None
        block = self._claim(name)
        if block is None:
            block = block_for(name, {}, classes='tool-call', expanded=self._tools_expanded)
            await self._append(block)
            self._last_block = block
        block.set_error(message)
        self._refresh_group()

    async def begin_response(self) -> None:
        """Marks the start of a new model response."""
        self._current = None
        self._open_tools.clear()
        self._close_group()

    async def _ensure_block(self) -> None:
        if self._current is None:
            self._current = AssistantBlock(
                reasoning_visible=self._reasoning_visible,
                classes='assistant-msg',
            )
            self._mark_filled()
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

    def finalize(self, *, close_pending: bool = True) -> None:
        """Flushes the transcript and optionally closes calls without a result."""
        for child in self.children:
            if isinstance(child, AssistantBlock):
                child.finalize()
            elif close_pending and isinstance(child, ToolCallBlock) and child.pending:
                child.set_error('Interrupted before a result was received.')
        self._refresh_group()

    def repaint(self) -> None:
        """Rebuilds every block so the text picks up the colors of the new theme.

        A `Static` caches the Rich renderable it was given, so the box would change
        color on a theme switch and the text inside it would keep the old palette.
        Asking each block to rebuild from its own state is what makes the live
        preview in the theme picker real.
        """
        self.refresh()
        for child in self.children:
            if isinstance(child, AssistantBlock):
                child.finalize()
            elif isinstance(child, ToolCallBlock):
                child.repaint()

    async def clear(self) -> None:
        self._current = None
        self._open_tools.clear()
        await self.remove_children()
        self._empty = True
        self.refresh()
        # After clearing, the new history follows the bottom again.
        self.anchor()

    async def render_history(
        self, messages: list[ModelMessage], *, subagents: Sequence[AgentNode] = (), from_agent: bool = False
    ) -> None:
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
        # `add_tool_result` does through the open-call registry.
        open_tools: dict[str, ToolCallBlock] = {}
        current: AssistantBlock | None = None
        # The batch being rebuilt. A `ModelResponse` is exactly one batch — the parts it
        # carries are the calls of one step — so the restored transcript does not have
        # to infer what a live turn has to infer: it reads it.
        group: ToolGroupHeader | None = None
        group_rows: list[ToolCallBlock] = []

        def close_group() -> None:
            nonlocal group, group_rows
            if group is not None:
                group.refresh_from(group_rows)
            group = None
            group_rows = []

        def follows_a_tool_block() -> bool:
            # The same rule a live turn applies: the margin keys off the block that
            # was appended last, not off the one still waiting for its result. A
            # result clears `open_tool`, and the next call still belongs to the run.
            return bool(batches) and isinstance(batches[-1], (ToolCallBlock, ToolGroupHeader))

        def block_for_tool(name: str, args: Any) -> ToolCallBlock:
            nonlocal group
            if group is None:
                group = ToolGroupHeader(classes='tool-group')
                batches.append(group)
            block = block_for(
                name,
                args,
                classes='tool-call tight' if follows_a_tool_block() else 'tool-call',
                expanded=self._tools_expanded,
            )
            group_rows.append(block)
            batches.append(block)
            return block

        for position, message in enumerate(messages):
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, UserPromptPart):
                        current = None
                        # The transcript is read ordered by `seq`, so the position in
                        # this list *is* the seq. That is what makes the click map to
                        # a turn without a side channel.
                        content = part.content
                        text = (
                            content
                            if isinstance(content, str)
                            else next((item for item in content if isinstance(item, str)), '')
                        )
                        reports, remaining = split_reports(text, subagents)
                        batches.extend(SubagentReport(node) for node in reports)
                        if reports and not isinstance(content, str):
                            content = [remaining if item is text else item for item in content]
                        elif reports:
                            content = remaining
                        if not reports or remaining or (not isinstance(content, str) and images_in(content)):
                            batches.append(_user_widget(content, seq=position, from_agent=from_agent))
                    elif isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_name:
                        ok = isinstance(part, ToolReturnPart) and part.outcome == 'success'
                        block = open_tools.pop(part.tool_call_id, None)
                        if block is None:
                            block = block_for_tool(part.tool_name, {})
                        block.set_result(part.content, ok=ok)
            elif isinstance(message, ModelResponse):
                for part in message.parts:
                    if isinstance(part, ThinkingPart):
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
                        open_tools[part.tool_call_id] = block_for_tool(part.tool_name, part.args)
                current = None
                close_group()

        if not batches:
            return
        self._mark_filled()
        for block in batches:
            if isinstance(block, SummonBlock):
                for node in subagents:
                    block.set_node(node)
        if self.is_mounted and self.app is not None:
            # One layout for the whole history instead of one per message.
            with self.app.batch_update():
                await self.mount_all(batches)
        else:
            for widget in batches:
                await self.mount(widget)
        close_group()
        self._current = None
        self._open_tools.clear()
        self._group = None
        self._group_rows = []
        self._last_block = batches[-1]

    async def render_items(
        self, items: Sequence[dict[str, Any]], *, pending: Sequence[dict[str, Any]] = ()
    ) -> list[Static]:
        """Paints a conversation the service already has, from its own projection.

        The items are the shape `src.server.views.transcript` defines — the same the
        web client paints — and not `ModelMessage`s: an attached terminal never holds
        the engine's objects, so it has to be able to draw a conversation from what the
        service sends. Nothing is inferred. A user line carries the text the user typed
        and the description of each image the projection provides; an assistant block
        carries the text and the reasoning it was given; a tool call is drawn from its
        name, its arguments and the result the service presented, so a stored call and
        a live one look the same.

        ``pending`` are the inputs the queue has accepted and the run has not read. They
        are painted *queued* and handed back, because a message that is waiting is not
        part of the conversation yet: the caller owns those widgets and takes each one
        down when the service confirms it. Reading them again later must not paint them
        twice, which is why they are returned rather than re-derived.

        Returns:
            The pending lines, in queue order, for the caller to retire.
        """
        batches: list[Static] = []
        open_tools: dict[str, ToolCallBlock] = {}
        current: AssistantBlock | None = None
        current_index: int | None = None

        for item in items:
            kind = str(item.get('kind') or '')
            index = item.get('index')
            if kind in {'assistant', 'thinking'}:
                text = str(item.get('text') or '')
                if not text:
                    continue
                if current is None or current_index != index:
                    current = AssistantBlock(reasoning_visible=self._reasoning_visible, classes='assistant-msg')
                    current_index = index
                    batches.append(current)
                if kind == 'thinking':
                    current.add_thinking(text)
                else:
                    current.add_text(text)
                continue
            # Anything that is not a continuation of the same response closes it.
            current = None
            current_index = None
            if kind == 'user':
                batches.append(
                    UserMessage(
                        _item_user_renderable(str(item.get('text') or ''), item.get('attachments') or ()),
                        seq=index if isinstance(index, int) else None,
                        classes='user-msg',
                    )
                )
            elif kind == 'tool_call':
                block = block_for(
                    str(item.get('tool_name') or ''),
                    item.get('args'),
                    classes='tool-call',
                    expanded=self._tools_expanded,
                )
                open_tools[str(item.get('call_id') or '')] = block
                batches.append(block)
            elif kind == 'tool_result':
                block = open_tools.pop(str(item.get('call_id') or ''), None)
                if block is None:
                    # A result whose call is not in this transcript (a compaction cut
                    # one off): the result is still true, so it is painted alone.
                    block = block_for(
                        str(item.get('tool_name') or ''),
                        {},
                        classes='tool-call',
                        expanded=self._tools_expanded,
                    )
                    batches.append(block)
                summary = item.get('summary')
                if isinstance(summary, dict):
                    block.set_summary(
                        ToolSummary(text=str(summary.get('text') or ''), status=str(summary.get('status') or 'ok'))
                    )
                block.set_result(str(item.get('text') or ''), ok=bool(item.get('ok', True)))

        queued = [
            UserMessage(
                _item_user_renderable(str(entry.get('text') or ''), (), queued=True),
                classes='user-msg',
            )
            for entry in pending
        ]
        batches.extend(queued)
        if batches:
            if self.is_mounted and self.app is not None:
                # One layout for the whole transcript, exactly as `render_history`
                # does: mounting one at a time makes restoring quadratic.
                with self.app.batch_update():
                    await self.mount_all(batches)
            else:
                for widget in batches:
                    await self.mount(widget)
            # `clear` put the mark back, because a cleared log looks exactly like a
            # session with nothing in it. This is the other half: something was
            # painted, so the mark is no longer the truth and stops being drawn
            # behind the conversation.
            self._mark_filled()
        # A call the transcript never answered stays visibly interrupted, the same way
        # it looks live; a healthy transcript is untouched by this.
        self.finalize()
        self.anchor()
        return queued


def _item_user_renderable(text: str, attachments: Sequence[Any], *, queued: bool = False) -> Text:
    """One user line of a stored conversation, from the service's projection.

    The projection describes an image instead of sending it — ``1 image (640×480 PNG)``
    — because the bytes stay on the server. So this is the sentence
    `describe_attachments` builds, made from the same fields it uses, and the
    ``[Image #n]`` marker stays in the text where the user wrote it: it is what the
    model read and therefore what a later question refers to.
    """
    line = Text(f'› {text}', style='bold')
    if queued:
        line.append('  · queued for the running turn', style='dim italic')
    labels = [str(view.get('label') or '') for view in attachments if isinstance(view, dict)]
    if labels:
        noun = 'image' if len(labels) == 1 else 'images'
        line.append(f'\n  {IMAGE_MARK} {len(labels)} {noun} ({", ".join(labels)})', style='dim')
    return line
