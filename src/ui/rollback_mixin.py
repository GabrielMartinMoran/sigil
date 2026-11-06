"""Going back to a message: the screen, the two actions, and the report.

The command and the click share every rule. What the click adds is the place: it opens
the list with the point you clicked already highlighted, because you were reading that
message when you decided.
"""

from __future__ import annotations

from textual.css.query import NoMatches

from src.engine.agents.rollback import RollbackError
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.rollback_screen import (
    FORK,
    ROLLBACK_FILES,
    RollbackChoice,
    RollbackScreen,
)


class RollbackMixin:
    """`/rollback`, and clicking a user message."""

    # --- entry points --------------------------------------------------------

    async def show_rollback(self, chat: ChatLog, args: str = '', *, preselected_seq: int | None = None) -> None:
        """Lists the points a session can go back to and does what was chosen.

        ``args`` is the index in the list or nothing. A number is a shortcut that skips
        the list; anything else opens it.
        """
        rollback = getattr(self, '_rollback', None)
        session = getattr(self, '_session', None)
        if rollback is None or session is None:
            await chat.add_notice('Rollback is not available in this session.', style='yellow')
            return
        if self._turn_active:
            await chat.add_notice('A turn is running: finish or stop it before going back.', style='yellow')
            return

        preselected = self._turn_of_seq(preselected_seq) if preselected_seq is not None else None
        points = self._rollback_points(rollback, session.id)
        if not points:
            await chat.add_notice('There is no message to go back to yet.', style='dim')
            return

        chosen = str(args).strip()
        shorthand = chosen.isdigit() and 1 <= int(chosen) <= len(points)
        if shorthand:
            preselected = points[int(chosen) - 1].checkpoint.turn_id

        choice = await self.push_screen_wait(
            RollbackScreen(points, preselected=preselected, start_on_actions=shorthand)
        )
        if choice is None:
            return
        await self.apply_rollback(chat, choice)

    def on_rollback_requested(self, message) -> None:
        """A user message was clicked: open the same screen, on that instant.

        In a worker and not directly: the handler runs in the message pump, and
        `push_screen_wait` raises `NoActiveWorker` outside one. That is why every
        screen in this app is opened from `run_worker`.
        """
        chat = self._chat_or_none()
        if chat is None:
            return
        self.run_worker(self.show_rollback(chat, preselected_seq=message.seq))

    # --- the work ------------------------------------------------------------

    async def apply_rollback(self, chat: ChatLog, choice: RollbackChoice) -> None:
        """Runs the chosen action and reports it honestly."""
        rollback = getattr(self, '_rollback', None)
        session = getattr(self, '_session', None)
        if rollback is None or session is None:
            return

        import asyncio

        if choice.action == FORK:
            try:
                outcome = await asyncio.to_thread(rollback.fork_to, session.id, choice.turn_id)
            except RollbackError as exc:
                await chat.add_notice(f'Could not fork: {exc}', style='red')
                return
            await chat.add_notice(
                f'Forked to {outcome.session_id[:8]} with {outcome.turns_copied} '
                f'turn{"" if outcome.turns_copied == 1 else "s"}. `/load` opens it; this one is untouched.',
                style='green',
            )
            return

        files = choice.action == ROLLBACK_FILES
        try:
            outcome = await asyncio.to_thread(lambda: rollback.to_turn(session.id, choice.turn_id, files=files))
        except RollbackError as exc:
            await chat.add_notice(f'Could not roll back: {exc}', style='red')
            return

        # The conversation on screen is the old one: it has to be rebuilt from the
        # rows, which are now shorter. The plan too, which the core already reloaded.
        await self.reload_session_view()
        await chat.add_notice(
            f'Rolled back: {outcome.turns_dropped} turn{"" if outcome.turns_dropped == 1 else "s"} dropped'
            + self._files_report(outcome)
            + f'. Undo with `/rollback {outcome.undo_checkpoint[:8]}` if you did not mean it.',
            style='green',
        )

    @staticmethod
    def _files_report(outcome) -> str:
        if outcome.files_unavailable:
            return ', workspace unchanged (no snapshot to restore)'
        if not outcome.touched_files:
            return ', no files changed'
        return f', {outcome.files_restored} restored and {outcome.files_deleted} deleted'

    # --- helpers -------------------------------------------------------------

    def _rollback_points(self, rollback, session_id: str):
        """The points, with the file cost of each, newest first.

        The cost is the same diff the restore would apply, so the screen is telling the
        truth rather than estimating: a rollback that says "3 files" touches three.
        """
        from src.ui.widgets.rollback_screen import Point

        points: list[Point] = []
        for checkpoint in rollback.points(session_id):
            preview = self._first_line_of_turn(session_id, checkpoint.turn_id)
            points.append(
                Point(
                    checkpoint=checkpoint,
                    preview=preview,
                    files=self._file_cost(rollback, checkpoint.tree_commit),
                )
            )
        return points

    def _file_cost(self, rollback, target_commit: str | None) -> int | None:
        """How many files a restore to ``target_commit`` would touch, or ``None``."""
        snapshots = getattr(rollback, 'snapshots', None)
        if target_commit is None or snapshots is None or not snapshots.available:
            return None
        try:
            current = snapshots.take('preview')
            if current is None:
                return None
            return len(snapshots.diff(current, target_commit))
        except Exception:  # noqa: BLE001 - a preview must never block the screen
            return None

    def _first_line_of_turn(self, session_id: str, turn_id: str | None) -> str:
        """The prompt that opened a turn, cut to one line."""
        store = getattr(self, '_store', None)
        if store is None or turn_id is None:
            return ''
        row = store.database.one(
            'SELECT m.payload FROM messages m WHERE m.turn_id = ? ORDER BY m.seq LIMIT 1',
            (turn_id,),
        )
        if row is None:
            return ''
        import json

        from src.engine.sessions.titling import shorten

        try:
            payload = json.loads(str(row['payload']))
        except ValueError:
            return ''
        for part in payload.get('parts') or []:
            content = part.get('content')
            if isinstance(content, str) and content.strip():
                return shorten(' '.join(content.split()))
            if isinstance(content, list):
                text = ' '.join(item for item in content if isinstance(item, str)).strip()
                if text:
                    return shorten(' '.join(text.split()))
        return ''

    def _turn_of_seq(self, seq: int) -> str | None:
        """Which turn starts the message at ``seq``."""
        store = getattr(self, '_store', None)
        session = getattr(self, '_session', None)
        if store is None or session is None:
            return None
        row = store.database.one(
            'SELECT turn_id FROM messages WHERE execution_id = ? AND seq = ?',
            (session.root_id, seq),
        )
        return None if row is None else str(row['turn_id'])

    async def reload_session_view(self) -> None:
        """Rebuilds the conversation on screen from its rows.

        A rollback shortened the transcript behind the app's back. Re-reading the
        session is cheaper and more honest than trying to remove the right widgets: the
        rows are the truth, and the screen has to agree with them.
        """
        store = getattr(self, '_store', None)
        session = getattr(self, '_session', None)
        if store is None or session is None:
            return
        reloaded = store.load(session.id)
        if reloaded is None:
            return
        self.bind_session(reloaded)
        chat = self._chat_or_none()
        if chat is None:
            return
        await chat.clear()
        await chat.render_history(reloaded.messages)

    def _chat_or_none(self):
        try:
            return self.query_one(ChatLog)
        except NoMatches:
            return None


__all__ = ['RollbackMixin']
