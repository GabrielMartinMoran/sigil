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

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

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
from rich.text import Text
from textual.color import Color
from textual.message import Message
from textual.widgets import Static

from src.core.agents.message_types import input_views
from src.core.agents.tree.models.agent_node import AgentNode
from src.core.attachments.content import describe_attachments, images_in
from src.core.attachments.image_attachment import ImageAttachment
from src.core.context.images import image_size
from src.core.tooling.tool_summary import ToolSummary
from src.ui.palette import color
from src.ui.widgets.agent_message_block import AgentMessageBlock
from src.ui.widgets.assistant_block import AssistantBlock
from src.ui.widgets.block_for import block_for
from src.ui.widgets.chat_style import MAX_MOUNTED_PAGES
from src.ui.widgets.chat_text import link_urls
from src.ui.widgets.empty_logo import logo_lines
from src.ui.widgets.link_notice import LinkNotice
from src.ui.widgets.read_only_scroll import ReadOnlyScroll
from src.ui.widgets.subagent_report import SubagentReport
from src.ui.widgets.summon_block import SummonBlock
from src.ui.widgets.tool_call_block import ToolCallBlock
from src.ui.widgets.tool_group_header import ToolGroupHeader
from src.ui.widgets.user_message import UserMessage

"""Seconds between Markdown re-renders while streaming.

Re-parsing on every delta is wasted work: the document changes by a few characters
and the parser walks the whole thing. The final flush is guaranteed by `finalize()`.
"""

"""Left edge of a tool block, on every line.

One continuous bar instead of a corner plus indentation: it draws the block as a box
on the left, which is what makes a run of calls read as one group hanging under the
answer that caused it, and it needs no closing line to look finished.
"""

"""Indentation for content under a line that is not a tool block."""

"""Output lines shown before the block truncates. Enough to recognize the result."""

"""Characters per output line kept before the block truncates a long one."""

"""Lines of output a failed call shows while collapsed.

The first line of a failure is usually the whole story (`FAILED test_x - NameError`),
the second and third give it context, and past that it is a stack trace nobody reads
in a transcript. More is a `/tools expanded` away.
"""

"""Marker of the collapsed reasoning line. Not an arrow: it is not an action."""

IMAGE_MARK = '▣'
"""Marker of the attachment summary line: a picture, in one column."""

"""How many pages of the conversation the log keeps mounted at once.

The bound is on pages, not on items, because a page is the unit the service hands
over and the unit the cursor walks back in: 100 items by default, so this is a few
hundred widgets of history behind and ahead of the reader instead of thousands. The
page let go of is the one furthest from the reader, and how it comes back depends on
where it was: a page that left above them is asked for again with the cursor it
carried, and a page that left below is read again with the newest window, because
the route has no forward cursor.
"""


@dataclass
class _Page:
    """One page of the mounted window, counted the two ways a page is read.

    ``widgets`` is what the screen holds; ``items`` is how many projected items those
    widgets stand for. The two differ because a page merges and omits: a thought and
    its answer share one block, and a tool call and its result share one row, so a
    page of a hundred items can mount sixty widgets.

    ``first_index`` is where the page starts in the transcript, as the service
    reported it. It is the cursor's only source: the window starts where its oldest
    page starts, so a page that leaves takes its own start with it and the next page's
    recorded start replaces it. Nothing about the cursor is re-derived from how many
    widgets a page happened to mount.
    """

    widgets: list[Static]
    items: int
    first_index: int


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

    The conversation itself is painted a page at a time. A long session is thousands
    of items, and mounting a widget for every one of them made attaching quadratic:
    the log holds a bounded window of pages, asks the service for the page above it
    when the reader reaches the top, and lets go of the page furthest from the reader
    once the window is full — the newest one for a reader at the top, and the one at
    the top for a reader who came back down. A window that let the bottom go does not
    end where the conversation ends, so reaching the bottom reads the newest page
    again. What the window holds is what the screen shows, not what the session has.
    """

    class OlderPageWanted(Message):
        """The reader reached the top of the mounted window and older items exist.

        The page above is the service's to send, so the ask is a message instead of a
        call: the widget knows where its window starts and nothing about the client.
        """

        def __init__(self, before: int) -> None:
            super().__init__()
            self.before = before

    class NewestPageWanted(Message):
        """The reader reached the bottom of a window that no longer ends there.

        What left the bottom cannot be asked for with a cursor — the route pages
        backwards only — so the repair is the read the screen opened with. Sent as a
        message for the same reason the page above is: the widget knows what its
        window holds, and the client is the app's.
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
        # The mounted window: the pages in reading order, each carrying the widgets it
        # mounted, the items it stands for and the position the service reported it
        # starts at — that start is the cursor for the page above — plus whether the
        # service says a page above the window exists.
        self._pages: list[_Page] = []
        self._has_more = False
        self._wanting_page = False
        # Whether the bottom of the window is still the end of the conversation, and
        # whether the read that repairs it is already on its way. A page let go of
        # below the reader takes the end with it, and there is no cursor that would
        # bring it back: the newest window is read again instead.
        self._reaches_end = True
        self._wanting_newest = False
        # The lines the queue overlay owns. They are never history and never leave
        # with a page, whatever the window does.
        self._pending_rows: list[Static] = []

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

    # --- the mounted window -------------------------------------------------

    @property
    def _page_first(self) -> int:
        """The transcript position the mounted window starts at, in items.

        It is read from the oldest mounted page, never re-derived: a page stands for
        more items than the widgets it mounts, and counting widgets would place the
        start short of where the page really began. ``0`` while nothing is mounted.
        """
        return self._pages[0].first_index if self._pages else 0

    @property
    def has_older(self) -> bool:
        """Whether the service says there is a page above the mounted window."""
        return self._has_more

    @property
    def older_cursor(self) -> int:
        """The ``before`` position that asks for the page above this one."""
        return self._page_first

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """Asks for the page above when the reader reaches the top of the window.

        The scroll position is the only signal there is: every way of moving — the
        wheel, `ctrl+↑`, a drag — ends here. One ask at a time, because a reader who
        keeps scrolling would otherwise send a request per row.
        """
        super().watch_scroll_y(old_value, new_value)
        if new_value <= 0:
            self.ask_for_older_page()
        if new_value >= self.max_scroll_y:
            self.ask_for_newest_page()

    def ask_for_older_page(self) -> None:
        """Posts for the page above, unless one is already on its way or there is none."""
        if self._wanting_page or not self._has_more or self._page_first <= 0:
            return
        if self.scroll_offset.y > 0:
            return
        self._wanting_page = True
        self.post_message(self.OlderPageWanted(self._page_first))

    def older_page_finished(self) -> None:
        """The ask ended without a page, so the reader may ask again.

        A page that failed is retried the next time the reader reaches the top, and
        nothing is printed for it: a notice would land at the bottom of the log and
        would close the block being streamed into.
        """
        self._wanting_page = False

    def ask_for_newest_page(self) -> None:
        """Posts for the newest page again, when the bottom is no longer the end.

        A window that let its newest page go has a hole below the mounted window, and
        the route pages backwards only: no cursor walks down. So the read the screen
        opened with is asked for again, once per arrival — while it is on its way, and
        after it lands, the window ends the conversation and a reader who keeps
        scrolling at the bottom asks for nothing more.
        """
        if self._wanting_newest or self._reaches_end:
            return
        if self.scroll_offset.y < self.max_scroll_y:
            return
        self._wanting_newest = True
        self.post_message(self.NewestPageWanted())

    def newest_page_finished(self) -> None:
        """The ask for the newest page ended without one, so the reader may ask again.

        Retried the next time they reach the bottom, and nothing is printed for the
        failure, for the same reason an older page prints nothing.
        """
        self._wanting_newest = False

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
            self._pending_rows.append(widget)
        # Taken as a tuple and kept private: the block repaints itself when the
        # queue mark goes away, and it has to remember what it was painting.
        widget._attachments = tuple(attachments)  # type: ignore[attr-defined]
        await self._append(widget)
        return widget

    def retire_pending(self, widget: Static) -> None:
        """Stops vouching for a waiting line the caller has taken down.

        The lines in `_pending_rows` are the ones `_is_live` protects from an eviction,
        so a line the app retires — the run read it, or its row stopped being pending —
        must stop being counted: holding a removed widget keeps it alive and protects
        nothing that is still painted. Retiring a line twice is a no-op, because a
        confirmation and a repaint can both retire the same row.
        """
        self._pending_rows = [row for row in self._pending_rows if row is not widget]

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
        # Nothing is mounted, so there is no window and no page above it.
        self._pages = []
        self._has_more = False
        self._wanting_page = False
        self._reaches_end = True
        self._wanting_newest = False
        self._pending_rows = []
        self.refresh()
        # After clearing, the new history follows the bottom again.
        self.anchor()

    async def render_history(self, messages: list[ModelMessage], *, subagents: Sequence[AgentNode] = ()) -> None:
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
                        for view in input_views(message, part):
                            if view['kind'] == 'subagent_report':
                                batches.append(
                                    SubagentReport(
                                        label=view['label'], short_id=view['short_id'], node_id=view['node_id']
                                    )
                                )
                            else:
                                batches.append(
                                    _user_widget(
                                        view['content'], seq=position, from_agent=view['kind'] == 'agent_message'
                                    )
                                )
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
        self,
        items: Sequence[dict[str, Any]],
        *,
        pending: Sequence[dict[str, Any]] = (),
        first_index: int = 0,
        has_more: bool = False,
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
        queued = [
            UserMessage(
                _item_user_renderable(str(entry.get('text') or ''), (), queued=True),
                classes='user-msg',
            )
            for entry in pending
        ]
        self._pending_rows = list(queued)
        await self._paint_newest(items, queued, first_index=first_index, has_more=has_more, close_pending=True)
        return queued

    async def reload_newest(
        self, items: Sequence[dict[str, Any]], *, first_index: int = 0, has_more: bool = False
    ) -> None:
        """Paints the newest page in place of the mounted window, keeping what is live.

        What left the bottom has no cursor to come back with, so the repair is the read
        the screen opened with, painted again where the window is: the pages go, and
        whatever belongs to no page — the block being streamed into and the lines the
        queue still holds — stays where it is. The anchor goes back to the bottom,
        because the bottom is what the reader asked for.

        A call still waiting for its result is left open: this is not a transcript
        rebuilt from stored rows, and marking a live call interrupted would be a lie.
        """
        mounted = self._pages
        self._pages = []
        if mounted:
            with self.app.batch_update():
                for page in mounted:
                    await self.remove_children(list(page.widgets))
        # What is left belongs to no page and stays under the new one.
        above = self.children[0] if self.children else None
        await self._paint_newest(
            items, (), first_index=first_index, has_more=has_more, close_pending=False, before=above
        )

    async def _paint_newest(
        self,
        items: Sequence[dict[str, Any]],
        queued: Sequence[Static],
        *,
        first_index: int,
        has_more: bool,
        close_pending: bool,
        before: Static | None = None,
    ) -> None:
        """Mounts the newest page as the whole window, and points the view at its end.

        The state is the window's, not the screen's: one page, the position it starts
        at, whether the service says anything older exists, and the end of the
        conversation restored. `before` is where the page goes when live widgets are
        already mounted below it.
        """
        widgets = self._page_widgets(items)
        tree_nodes = getattr(self.app, 'tree_nodes', None) if self.is_mounted else None
        if callable(tree_nodes):
            for block in widgets:
                if isinstance(block, SummonBlock):
                    for node in tree_nodes():
                        block.set_node(node)
        # The newest page is the whole window, and it records both counts plus the
        # position the service reported: a page kept only as its widgets could not say
        # where the window starts once it is no longer the newest one.
        self._pages = [_Page(widgets=widgets, items=len(items), first_index=first_index)] if widgets else []
        self._has_more = has_more
        self._wanting_page = False
        self._wanting_newest = False
        self._reaches_end = True
        batches = widgets + list(queued)
        if batches:
            # Marked before the mount, not after it: mounting awaits, and a frame in
            # between would draw the conversation and the empty mark at once. The
            # batch is what makes the log non-empty, not the mount finishing.
            self._mark_filled()
            if self.is_mounted and self.app is not None:
                # One layout for the whole page, exactly as `render_history` does:
                # mounting one at a time makes restoring quadratic.
                with self.app.batch_update():
                    await self.mount_all(batches, before=before)
            else:
                for widget in batches:
                    await self.mount(widget)
        # A call the transcript never answered stays visibly interrupted, the same way
        # it looks live; a healthy transcript is untouched by this.
        self.finalize(close_pending=close_pending)
        self.anchor()

    def _page_widgets(self, items: Sequence[dict[str, Any]]) -> list[Static]:
        """The blocks one page of the service's projection paints, in order.

        The shapes are the ones `render_history` reads, taken from the flat
        projection instead of the engine's messages. The margin rule is the same one
        a live turn and a restored history apply: a tool row that follows a tool row
        gets no gap of its own. It is decided here from the rows of this page and
        never remembered between pages, so a page boundary cannot change how a batch
        reads.
        """
        blocks: list[Static] = []
        open_tools: dict[str, ToolCallBlock] = {}
        current: AssistantBlock | None = None
        current_index: int | None = None
        follows_tool_block = False

        def open_block(name: str, args: Any) -> ToolCallBlock:
            nonlocal follows_tool_block
            block = block_for(
                name,
                args,
                classes='tool-call tight' if follows_tool_block else 'tool-call',
                expanded=self._tools_expanded,
            )
            blocks.append(block)
            follows_tool_block = True
            return block

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
                    blocks.append(current)
                    follows_tool_block = False
                if kind == 'thinking':
                    current.add_thinking(text)
                else:
                    current.add_text(text)
                continue
            # Anything that is not a continuation of the same response closes it.
            current = None
            current_index = None
            if kind == 'agent_message':
                blocks.append(_user_widget(str(item.get('text') or ''), from_agent=True))
                follows_tool_block = False
            elif kind == 'agent_notice':
                blocks.append(Static(Text(str(item.get('text') or ''), style='dim')))
                follows_tool_block = False
            elif kind == 'user':
                blocks.append(
                    UserMessage(
                        _item_user_renderable(str(item.get('text') or ''), item.get('attachments') or ()),
                        seq=index if isinstance(index, int) else None,
                        classes='user-msg',
                    )
                )
                follows_tool_block = False
            elif kind == 'subagent_report':
                blocks.append(
                    SubagentReport(
                        label=str(item.get('label') or ''),
                        short_id=str(item.get('short_id') or ''),
                        node_id=str(item.get('node_id') or ''),
                    )
                )
                follows_tool_block = False
            elif kind == 'tool_call':
                call_id = str(item.get('call_id') or '')
                open_tools[call_id] = open_block(str(item.get('tool_name') or ''), item.get('args'))
            elif kind == 'tool_result':
                block = open_tools.pop(str(item.get('call_id') or ''), None)
                if block is None:
                    # A result whose call is not in this page — a compaction cut one
                    # off, or a page boundary split the pair — is still true, so it is
                    # painted alone.
                    block = open_block(str(item.get('tool_name') or ''), {})
                summary = item.get('summary')
                if isinstance(summary, dict):
                    block.set_summary(
                        ToolSummary(text=str(summary.get('text') or ''), status=str(summary.get('status') or 'ok'))
                    )
                block.set_result(str(item.get('text') or ''), ok=bool(item.get('ok', True)))
        return blocks

    async def prepend_items(self, items: Sequence[dict[str, Any]], *, first_index: int, has_more: bool) -> None:
        """Paints one page above the mounted window, keeping the reader in place.

        The page is recorded with both counts — the widgets it mounted and the items it
        stands for — and with the position the service said it starts at. The window's
        cursor is read from that record, so it is never restored by arithmetic: when the
        page is later let go of, the page below reports its own start and that becomes
        the cursor. A page that mounted fewer widgets than it held items cannot shorten
        the walk.

        The page is mounted at the top and the scroll is moved by exactly the height it
        added, so the row the reader was looking at stays on the same screen row. Both
        numbers are taken around the layout that paints the page: the height cannot be
        guessed from the items, and the position, once measured, is not derived again
        from a total that has changed.

        Where the reader is is captured here, before the insertion, and handed to the
        eviction: the page arrives above them, so they keep looking at the same content
        while the offset grows, and an offset read after the insertion would describe a
        row they are not on. The position is taken in the window as it is now, in
        mounted widgets, and the page's own widgets move it along the window that is
        about to exist.
        """
        self._has_more = has_more
        self._wanting_page = False
        widgets = self._page_widgets(items)
        position = self._reading_widget_position()
        below = self.children[0] if self.children else None
        reading = None if below is None else below.virtual_region.y - self.scroll_offset.y
        self._close_page(widgets)
        if widgets:
            if below is None:
                await self.mount_all(widgets)
            else:
                with self.app.batch_update():
                    await self.mount_all(widgets, before=below)
                self._settle_seam(widgets, below)
            self._mark_filled()
            if below is not None and reading is not None:
                self.call_after_refresh(self._keep_reading, below, reading)
        # Recorded even when it paints nothing: the page still spans its items, and
        # forgetting where it started would ask for it a second time.
        self._pages.insert(0, _Page(widgets=widgets, items=len(items), first_index=first_index))
        await self._drop_far_pages(position + len(widgets))

    def _close_page(self, page: Sequence[Static]) -> None:
        """Flushes one page's blocks without touching the ones being written.

        `finalize()` walks the whole log, which mid-turn would also close the call the
        run is streaming into. A page is closed on its own, and a call whose result
        arrived in the page below reads as interrupted — which is how a split pair has
        looked since the transcript was first rebuilt from rows.
        """
        for widget in page:
            if isinstance(widget, AssistantBlock):
                widget.finalize()
            elif isinstance(widget, ToolCallBlock) and widget.pending:
                widget.set_error('Interrupted before a result was received.')

    def _settle_seam(self, page: Sequence[Static], below: Static) -> None:
        """Gives the row under a page boundary the margin its new neighbour asks for."""
        if not page or not isinstance(below, ToolCallBlock):
            return
        below.set_class(isinstance(page[-1], (ToolCallBlock, ToolGroupHeader)), 'tight')

    def _keep_reading(self, anchor: Static, row: float) -> None:
        """Puts ``anchor`` back on the screen row it had before the insertion."""
        self.scroll_to(y=anchor.virtual_region.y - row, animate=False, immediate=True, release_anchor=False)

    async def _drop_far_pages(self, reading: float) -> None:
        """Lets go of the page furthest from the reader while the window is over its bound.

        Distance, not age, decides which page leaves. A reader at the top is furthest
        from the newest page, so that is the one let go of and the window stops ending
        where the conversation ends; a reader who came back down is furthest from the
        page at the top, which is the page a cursor can ask for again. The page the
        screen shows least of is the one whose leaving moves nothing.

        ``reading`` is the reader's position among the mounted **widgets**, captured
        before the page above was inserted and already moved along by the widgets it
        added. Widgets are the unit here on purpose: the question is how much of the
        screen sits between the reader and a page, and that is a matter of mounted rows,
        not of how many items those rows stand for. The cursor is the other unit and is
        never moved by counting widgets: a page that leaves from the top takes its own
        recorded start with it, and the page below reports the start that replaces it.

        The block being streamed into and the queue's waiting lines belong to no page
        and are never candidates: a page that somehow held one is skipped, and when
        every page holds something live the window grows past its bound rather than
        losing what is live.
        """
        while len(self._pages) > MAX_MOUNTED_PAGES:
            farthest = self._farthest_page(reading)
            if farthest is None:
                break
            newest = len(self._pages) - 1
            above = sum(len(page.widgets) for page in self._pages[:farthest])
            page = self._pages.pop(farthest)
            if farthest == 0:
                # The window now starts where the page that left ended: the page below
                # carries that start as the ``first_index`` the service reported, in
                # items. The page that left is itself the proof that something older
                # exists.
                self._has_more = True
            if farthest == newest:
                # The newest page left with the end of the conversation: the bottom of
                # the window is no longer the bottom of the transcript.
                self._reaches_end = False
            # The window the position is measured in is shorter now, above the reader.
            if reading >= above + len(page.widgets):
                reading -= len(page.widgets)
            elif reading > above:
                reading = float(above)
            await self.remove_children(list(page.widgets))

    def _farthest_page(self, reading: float) -> int | None:
        """Index of the mounted page furthest from ``reading``, or `None` for no candidate.

        A page holding a live widget is not a candidate. A page the reader is inside is
        at distance zero and cannot win while another page is mounted, so the page
        being read is never the one let go of. Ties go to the page nearest the top,
        which is the side a cursor can walk back into.
        """
        farthest: int | None = None
        reach = -1.0
        for index in range(len(self._pages)):
            if any(self._is_live(widget) for widget in self._pages[index].widgets):
                continue
            distance = self._widget_distance(index, reading)
            if distance > reach:
                farthest = index
                reach = distance
        return farthest

    def _reading_widget_position(self) -> float:
        """Where the reader is among the mounted widgets, as a position in the window.

        The scroll gives a fraction of the content — 0 at its top, 1 at its bottom,
        read from the middle of the visible rows because a screen shows a span and not
        a row — and that fraction becomes a position among the mounted widgets. Widgets
        are the unit on purpose: the eviction asks how much of the screen lies between
        the reader and a page, and a page holding more items than widgets is not further
        away for it. Items are the cursor's unit and are not counted here.

        Called before a page is inserted, never after: the offset the reader holds is
        the one of the window they are reading, and the insertion above them moves the
        offset, not the row they are on.
        """
        widgets = sum(len(page.widgets) for page in self._pages)
        visible = self.container_size.height or 1
        content = self.max_scroll_y + visible
        if widgets <= 0 or content <= 0:
            return 0.0
        fraction = min(1.0, max(0.0, (self.scroll_offset.y + visible / 2) / content))
        return fraction * widgets

    def _widget_distance(self, index: int, reading: float) -> float:
        """How far the reader is from one mounted page, in widgets, to its nearest edge.

        Widgets, because the page whose leaving moves least is the one the screen shows
        least of. A page the reader is inside is at distance zero, so holding the
        position is worth more than being one row further away.
        """
        start = sum(len(page.widgets) for page in self._pages[:index])
        end = start + len(self._pages[index].widgets) - 1
        if reading < start:
            return start - reading
        if reading > end:
            return reading - end
        return 0.0

    def _is_live(self, widget: Static) -> bool:
        """Whether the transcript must keep the widget whatever the window does."""
        return widget is self._current or widget in self._pending_rows


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
