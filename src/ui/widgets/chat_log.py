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
from textual.widgets import Static

from src.engine.attachments.content import describe_attachments, images_in
from src.engine.attachments.image_attachment import ImageAttachment
from src.engine.context.images import image_size
from src.engine.tooling.presentation import ToolSummary, display_name
from src.ui.messages import NodeOpened, RollbackRequested
from src.ui.palette import color
from src.ui.widgets.reading import ReadOnlyScroll
from src.ui.widgets.rows import fit

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
    lines = [line.rstrip() for line in _raw_output(value).splitlines()]
    # A trailing blank line is an artefact of `print`-style output, not content.
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _raw_output(value: Any) -> str:
    """The text of a result, preferring a process's own output over its repr."""
    stdout = getattr(value, 'stdout', None)
    stderr = getattr(value, 'stderr', None)
    if stdout is None and stderr is None:
        try:
            return value if isinstance(value, str) else str(value)
        except Exception:  # noqa: BLE001 - see `preview`
            return object.__repr__(value)
    # stderr last: a command that worked and complained reads better that way, and a
    # failure puts its reason at the bottom, which is where the eye lands.
    parts = [part for part in (stdout, stderr) if part]
    return '\n'.join(parts) if parts else f'exit {getattr(value, "returncode", "?")}'


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


def _user_widget(content: str | list[Any], *, seq: int | None = None) -> Static:
    """One user line, from a plain prompt or from text plus images.

    Both shapes reach `render_history`: a turn typed before images existed saved
    its prompt as a string, and one with a pasted screenshot saved it as a list of
    text and `BinaryContent`. The marker `[Image #n]` is already in the text, so
    the images only have to be summarised back into the line under it.
    """
    if isinstance(content, str):
        return UserMessage(_user_renderable(content, queued=False), seq=seq, classes='user-msg')
    text = ' '.join(item for item in content if isinstance(item, str))
    described = _describe_parts(images_in(content))
    return UserMessage(_user_renderable(text, queued=False, attachments=described), seq=seq, classes='user-msg')


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

    The header carries the call and, flush right, what came of it: how long it took
    and the one line the tool chose to describe its result (`9 passed in 0.45s`,
    `3 replacements`). That summary comes from the tool itself — see
    `engine.tooling.presentation` — and not from `str(result)`, which is how a
    Pydantic repr used to end up in the middle of a conversation.

    Two things are deliberately separate. The **glyph** says whether the call itself
    came back (`✓`) or raised (`✗`). The **color of the summary** says how it went
    (`good`, `bad`): a test run that fails is a successful call with a bad result, and
    painting that with `✗` would make it indistinguishable from a broken tool.

    The body carries the beginning of the output, dimmed.
    """

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
        self._summary: ToolSummary | None = None
        self._duration_ms: float | None = None
        self._prepare()
        self._update_content()

    def _prepare(self) -> None:
        """Hook for a subclass that needs its own state before the first paint.

        `__init__` paints once at the end, and a subclass that set its fields after
        `super().__init__` would be painted with none of them.
        """

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
        self._result = output
        self._ok = ok
        self._pending = False
        self._update_content()

    def set_error(self, message: str) -> None:
        self._error = message
        self._ok = False
        self._pending = False
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

    def repaint(self) -> None:
        """Rebuilds the block after a theme change; see `ChatLog.repaint`."""
        self._update_content()

    def on_resize(self) -> None:
        """Rebuilds the header: the right column is aligned to the current width."""
        self._update_content()

    def _update_content(self) -> None:
        self.update(self._build())

    def _build(self):
        lines: list[Text] = [self._header()]
        lines.extend(self._body())
        return Group(*lines)

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
        text.append(f'{self._state_mark()} ', style=border)
        text.append(display_name(self._name), style=f'bold {color(self, "tool-title")}')
        arguments = format_tool_args(self._args)
        if arguments:
            text.append(f' {arguments}', style=color(self, 'muted'))
        return fit(text, self._outcome(), self.size.width)

    def _outcome(self) -> Text | None:
        """`9 passed in 0.45s · 1.2s`: the tool's summary, then how long it took.

        Nothing while the call runs — the `…` mark already says that a timer would be
        the only thing moving. Nothing after a reload either: the duration lives in the
        event and is not persisted, and `0.0s` would be a lie.
        """
        if self._pending:
            return None
        parts: list[tuple[str, str]] = []
        if self._summary is not None:
            parts.append((self._summary.text, self._status_style(self._summary.status)))
        elapsed = format_duration(self._duration_ms)
        if elapsed:
            parts.append((elapsed, color(self, 'muted')))
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

        The color is the second axis: it says how the call went, while the glyph says
        whether it came back at all.
        """
        return {'good': color(self, 'success'), 'bad': color(self, 'error')}.get(status, color(self, 'muted'))

    def _state_mark(self) -> str:
        """`…` while running, `✓` or `✗` after. One column, so nothing shifts."""
        if self._pending:
            return '…'
        return '✓' if self._ok else '✗'

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

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._rows: list[ToolCallBlock] = []

    def refresh_from(self, rows: Sequence[ToolCallBlock]) -> None:
        """Repaints from the rows the transcript is holding for this batch."""
        self._rows = list(rows)
        self.display = len(self._rows) >= MIN_GROUP_ROWS
        self.update(self._build())

    def on_resize(self) -> None:
        self.update(self._build())

    @property
    def closed(self) -> bool:
        """Whether every row has an outcome: the batch is over."""
        return all(not row.pending for row in self._rows)

    def _build(self) -> Text:
        rows = self._rows
        total = len(rows)
        done = sum(1 for row in rows if not row.pending)
        errors = sum(1 for row in rows if row.raised)
        text = Text(no_wrap=True, overflow='ellipsis')
        text.append(GUTTER, style=color(self, 'tool-border'))
        if not self.closed:
            text.append('⋯ ', style=color(self, 'tool-border'))
        text.append(f'{total} tool' + ('s' if total != 1 else ''), style=f'bold {color(self, "tool-title")}')
        if not self.closed:
            text.append(f'  [{done}/{total}]', style=color(self, 'muted'))
        if errors:
            text.append(' · ', style=color(self, 'muted'))
            text.append(f'{errors} error' + ('s' if errors != 1 else ''), style=color(self, 'error'))
        # The batch's duration is its longest row, not the sum: the calls ran together,
        # so adding them up would invent a number nobody waited for.
        elapsed = format_duration(self._longest())
        if elapsed and self.closed:
            text.append(' · ', style=color(self, 'muted'))
            text.append(elapsed, style=color(self, 'muted'))
        if self.size.width and text.cell_len > self.size.width:
            text.truncate(self.size.width, overflow='ellipsis')
        return text

    def _longest(self) -> float | None:
        durations = [row.duration_ms for row in self._rows if row.duration_ms is not None]
        return max(durations) if durations else None


class SummonBlock(ToolCallBlock):
    """A subagent being started, drawn as a hand-off and not as a tool call.

    `summon` is a tool, and for a moment it looked like one: a name, a JSON blob and
    a state mark. But what the user is watching is not a call — it is another agent
    beginning to work, and the id in the result is the handle they will use for the
    rest of the session. So the block reads as a spawn:

        ▶ Scout · pulsar-researcher
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
            'agent_id': str(args.get('agent_id') or ''),
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
        # envelope: `[subagent Scout (scout #1a2b3c, sync) finished]`.
        named = re.search(r'\(([a-z0-9][a-z0-9-]*)\s+#', text)
        if named:
            self._node_name = named.group(1)
        self._status = self._read_status(text, ok=ok)

    def set_error(self, message: str) -> None:
        super().set_error(message)
        self._status = 'refused'

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
        if 'is working in the background' in lowered or 'working on it now' in lowered:
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
        text.append('↻ ' if resumed else '▶ ', style=f'bold {color(self, "success")}')
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
        else:
            # Nothing to go on: the block still has to say *what* this is, and the
            # arrow already says it is a spawn. No invented agent name.
            text.append('(unknown agent)', style=color(self, 'muted'))
        if resumed:
            text.append(f'  resume #{self._spawn["agent_id"][:8]}', style=color(self, 'muted'))
        return text

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
        if self._node_id:
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

    def __init__(self, *, reasoning_visible: bool = False, tools_expanded: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._tools_expanded = tools_expanded
        self._current: AssistantBlock | None = None
        self._open_tools: dict[str, list[ToolCallBlock]] = {}
        # The header of the batch being drawn, and its rows. `None` between batches.
        self._group: ToolGroupHeader | None = None
        self._group_rows: list[ToolCallBlock] = []
        self._last_block: Static | None = None
        self._reasoning_visible = reasoning_visible

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
        await self._append(Static(Text(text, style=style), classes='notice'))

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

    async def add_tool_result(self, name: str, output: Any, *, ok: bool = True) -> None:
        """Closes the open block, or starts one when the call was not announced.

        A result can arrive without a `start` event: a tool that fails before its
        wrapper runs, or a history replayed from a session saved before this block
        existed. Drawing a block with only the result keeps the transcript whole.
        """
        block = self._claim(name)
        if block is None:
            block = block_for(name, {}, classes='tool-call', expanded=self._tools_expanded)
            await self._append(block)
            self._last_block = block
        block.set_result(output, ok=ok)
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
        self._open_tools.clear()
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
        # `add_tool_result` does through the open-call registry.
        open_tool: ToolCallBlock | None = None
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
            nonlocal open_tool, group
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
            open_tool = block
            return block

        for position, message in enumerate(messages):
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, UserPromptPart):
                        current = None
                        open_tool = None
                        # The transcript is read ordered by `seq`, so the position in
                        # this list *is* the seq. That is what makes the click map to
                        # a turn without a side channel.
                        batches.append(_user_widget(part.content, seq=position))
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
                close_group()

        if not batches:
            return
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
