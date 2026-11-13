"""Going back to a message: the screen, the two actions, and the report.

The command and the click share every rule. What the click adds is the place: it opens
the list with the point you clicked already highlighted, because you were reading that
message when you decided.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from typing import Any

from textual.css.query import NoMatches

from src.engine.agents.rollback import Checkpoint, RollbackError, prompt_of_turn
from src.engine.attachments.image_attachment import ImageAttachment, new_image_id
from src.engine.attachments.tokens import numbers_in
from src.engine.context.images import image_size
from src.engine.sessions.titling import shorten
from src.ui.service_client import ServiceClient, ServiceClientError
from src.ui.turn_mixin import failure_notice
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.rollback_screen import (
    FORK,
    ROLLBACK_FILES,
    Point,
    RollbackChoice,
    RollbackScreen,
)


def _turn_for_seq(rows: Sequence[Mapping[str, Any]], seq: int | None) -> str | None:
    """The turn a clicked transcript position belongs to, or ``None``.

    A click on a message carries the `seq` it was painted at; a rollback point
    carries a turn id. `/api/rollback` projects both, so the mapping is a lookup and
    not a guess — and it is a function so the preselection can be tested without
    opening a screen.
    """
    if seq is None:
        return None
    for row in rows:
        if row.get('seq') == seq:
            turn_id = row.get('turn_id')
            return None if turn_id is None else str(turn_id)
    return None


def _binary_image(row: Mapping[str, Any]) -> Any:
    """One restored image, decoded from the route's base64 back to bytes.

    The composer wants the payload it can attach, not a path or a promise: this is
    the same `BinaryContent` the turn carried, so the markers in the restored text
    still point at their pictures.
    """
    from pydantic_ai.messages import BinaryContent

    return BinaryContent(
        data=base64.b64decode(str(row.get('data') or '')),
        media_type=str(row.get('media_type') or 'application/octet-stream'),
        identifier=row.get('identifier'),
    )


def _files_report(*, unavailable: bool, restored: int, deleted: int) -> str:
    """What a rollback did to the workspace, in the words both branches use.

    One function so the attached terminal cannot drift from the one that owns the
    engine: a rollback that could not restore files has to say so, and a notice
    that only counted turns would be reporting half of what happened.
    """
    if unavailable:
        return ', workspace unchanged (no snapshot to restore)'
    if not (restored or deleted):
        return ', no files changed'
    return f', {restored} restored and {deleted} deleted'


def _projected_point(row: Mapping[str, Any]) -> Point:
    """One projected point, rebuilt as the row the screen paints.

    The screen reads the turn id and the moment, so those come from the projection.
    The other ``Checkpoint`` fields are carried empty because ``/api/rollback`` does
    not send them, and ``files`` stays ``None`` because the route deliberately omits
    the file cost of a restore: a guess would read as a promise.
    """
    turn_id = str(row.get('turn_id') or '')
    return Point(
        checkpoint=Checkpoint(
            id=turn_id,
            session_id='',
            execution_id='',
            turn_id=turn_id,
            at=str(row.get('at') or ''),
            reason=str(row.get('reason') or ''),
            label=str(row.get('label') or ''),
            tree_commit=row.get('tree_commit'),
        ),
        preview=str(row.get('preview') or ''),
        files=None,
    )


class RollbackMixin:
    """`/rollback`, and clicking a user message."""

    # --- entry points --------------------------------------------------------

    async def show_rollback(self, chat: ChatLog, args: str = '', *, preselected_seq: int | None = None) -> None:
        """Lists the points a session can go back to and does what was chosen.

        ``args`` is the index in the list or nothing. A number is a shortcut that skips
        the list; anything else opens it.

        An attached terminal delegates both actions: the points, the truncation and
        the fork belong to the session's owner. The screen stays here, and the index
        shortcut keeps its meaning because the service lists the points in the same
        order the terminal has always shown them.
        """
        if self._client is not None:
            await self._service_rollback(self._client, chat, args, preselected_seq=preselected_seq)
            return
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

    async def _service_rollback(
        self, client: ServiceClient, chat: ChatLog, args: str, *, preselected_seq: int | None = None
    ) -> None:
        """Lists the service's points, opens the same screen, applies the choice there."""
        if self._turn_active:
            await chat.add_notice('A turn is running: finish or stop it before going back.', style='yellow')
            return
        try:
            listing = await client.rollback_points()
        except ServiceClientError as exc:
            await chat.add_notice(f'✖ {failure_notice(exc)}', style='bold red')
            return
        points = [_projected_point(row) for row in listing.get('points') or []]
        if not points:
            await chat.add_notice('There is no message to go back to yet.', style='dim')
            return

        chosen = str(args).strip()
        shorthand = chosen.isdigit() and 1 <= int(chosen) <= len(points)
        preselected = points[int(chosen) - 1].checkpoint.turn_id if shorthand else None
        if preselected is None and preselected_seq is not None:
            # The click carries a transcript position; the point carries a turn id.
            # `seq` on the projection is what maps one to the other.
            preselected = _turn_for_seq(listing.get('points') or [], preselected_seq)
        choice = await self.push_screen_wait(
            RollbackScreen(points, preselected=preselected, start_on_actions=shorthand)
        )
        if choice is None:
            return
        await self._service_apply_rollback(client, chat, choice)

    async def _service_apply_rollback(self, client: ServiceClient, chat: ChatLog, choice: RollbackChoice) -> None:
        """Runs the chosen action in the service and repaints from its answer."""
        if choice.action == FORK:
            try:
                payload = await client.fork(choice.turn_id)
            except ServiceClientError as exc:
                await chat.add_notice(f'✖ Could not fork: {failure_notice(exc)}', style='red')
                return
            forked = payload.get('fork') or {}
            turns = int(forked.get('turns_copied') or 0)
            await chat.add_notice(
                f'Forked to {str(forked.get("session_id") or "")[:8]} with {turns} '
                f'turn{"" if turns == 1 else "s"}. `/load` opens it; this one is untouched.',
                style='green',
            )
            return

        files = choice.action == ROLLBACK_FILES
        try:
            payload = await client.rollback(choice.turn_id, files=files)
        except ServiceClientError as exc:
            await chat.add_notice(f'✖ Could not roll back: {failure_notice(exc)}', style='red')
            return

        # The rows are shorter now, so the transcript is rebuilt from the service.
        await chat.clear()
        await self.refresh_service_state()
        await self.load_service_history(rebuild=True)
        self._service_cursor = self._state_cursor()

        outcome = payload.get('rollback') or {}
        dropped = int(outcome.get('turns_dropped') or 0)
        files_note = _files_report(
            unavailable=bool(outcome.get('files_unavailable')),
            restored=int(outcome.get('files_restored') or 0),
            deleted=int(outcome.get('files_deleted') or 0),
        )
        undo = str(outcome.get('undo_checkpoint') or '')
        await chat.add_notice(
            f'Rolled back: {dropped} turn{"" if dropped == 1 else "s"} dropped'
            + files_note
            + (f'. Undo with `/rollback {undo[:8]}` if you did not mean it.' if undo else '.'),
            style='green',
        )
        prompt = outcome.get('prompt')
        images = [_binary_image(row) for row in outcome.get('prompt_images') or []]
        if (isinstance(prompt, str) and prompt.strip()) or images:
            self._restore_prompt((prompt if isinstance(prompt, str) else '', images))

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
        # Read before the truncation: the prompt is in the rows the rollback drops.
        store = getattr(self, '_store', None)
        prompt = None if store is None else prompt_of_turn(store.database, choice.turn_id)
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
        self._restore_prompt(prompt)

    @staticmethod
    def _files_report(outcome: Any) -> str:
        return _files_report(
            unavailable=bool(outcome.files_unavailable),
            restored=int(outcome.files_restored),
            deleted=int(outcome.files_deleted),
        )

    # --- helpers -------------------------------------------------------------

    def _restore_prompt(self, prompt: tuple[str, list] | None) -> None:
        """Puts the turn's prompt back in the composer, ready to be sent again.

        A rollback is "put me back where I was before I sent this", and what the user
        does next is say it differently: retyping it from the transcript is work the
        app can do. The images travel with it — a message whose text came back without
        its picture points at an `[Image #1]` that no longer exists — and the composer
        takes the focus so `Enter` sends.

        A draft already in the composer is not thrown away: it stays as the message
        above, and the restored prompt lands under it, which is the line the cursor
        ends on.
        """
        if prompt is None:
            return
        text, images = prompt
        if not text.strip() and not images:
            return
        try:
            composer = self.query_one(PromptInput)
        except NoMatches:
            return
        entries: list[tuple[str, list[ImageAttachment]]] = []
        if composer.text.strip() or composer.attachments:
            entries.append((composer.text, list(composer.attachments)))
        attachments: list[ImageAttachment] = []
        for number, image in enumerate(images, start=1):
            width, height = image_size(image.data)
            attachments.append(
                ImageAttachment(
                    number=number,
                    data=image.data,
                    media_type=image.media_type or 'application/octet-stream',
                    width=width,
                    height=height,
                    image_id=image.identifier or new_image_id(),
                )
            )
        entries.append((_with_every_marker(text, len(images)), attachments))
        composer.restore(entries)
        composer.focus()

    def _rollback_points(self, rollback: Any, session_id: str) -> list[Point]:
        """The points, with the file cost of each, newest first.

        The cost is the same diff the restore would apply, so the screen is telling the
        truth rather than estimating: a rollback that says "3 files" touches three.
        """
        from src.ui.widgets.rollback_screen import Point

        points: list[Point] = []
        for checkpoint in rollback.points(session_id):
            preview = self._first_line_of_turn(checkpoint.turn_id)
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

    def _first_line_of_turn(self, turn_id: str | None) -> str:
        """The prompt that opened a turn, cut to one line.

        From the prompt and not from the turn's first message: a point is identified
        by what the user typed, and the first message of a turn can be a thought, a
        tool result or a fragment. That mismatch is what once listed a rollback point
        whose preview was an answer the agent wrote.
        """
        store = getattr(self, '_store', None)
        if store is None or turn_id is None:
            return ''
        prompt = prompt_of_turn(store.database, turn_id)
        if prompt is None:
            return ''
        text, images = prompt
        line = shorten(' '.join(text.split()))
        if not images:
            return line
        plural = 'image' if len(images) == 1 else 'images'
        count = f'+{len(images)} {plural}'
        return f'{line} {count}'.strip()

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
        await chat.render_history(reloaded.messages, subagents=self.tree_nodes())
        chat.finalize()

    def _chat_or_none(self) -> ChatLog | None:
        try:
            return self.query_one(ChatLog)
        except NoMatches:
            return None


def _with_every_marker(text: str, images: int) -> str:
    """The prompt with a `[Image #n]` for every image it brings back.

    A message can carry a picture with no words — a screenshot pasted and sent — and
    the composer's contract is that the marker is what stands for the image. Without
    one the image is unreachable and the first edit drops it, so the markers the text
    is missing are appended in order.
    """
    if not images:
        return text
    missing = [number for number in range(1, images + 1) if number not in numbers_in(text)]
    if not missing:
        return text
    markers = ' '.join(f'[Image #{number}]' for number in missing)
    return f'{text} {markers}'.strip()


__all__ = ['RollbackMixin']
