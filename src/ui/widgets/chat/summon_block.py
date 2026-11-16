"""The block that shows a subagent being summoned."""

from __future__ import annotations

import json
import re
from typing import Any

from rich.console import Group
from rich.text import Text

from src.core.agents.tree.models.agent_node import AgentNode
from src.ui.messages.node_opened import NodeOpened
from src.ui.render.palette import color
from src.ui.widgets.chat.chat_style import GUTTER, TOOL_OUTPUT_WIDTH
from src.ui.widgets.chat.tool_call_block import ToolCallBlock
from src.ui.widgets.panels.status_bar import SPINNER_FRAMES, SPINNER_INTERVAL


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
        self._background_running = self._status == 'working'
        if self._background_running and self.is_mounted and self._pending_timer is None:
            self._pending_timer = self.set_interval(SPINNER_INTERVAL, self._advance_pending)
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
