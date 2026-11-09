"""Summoning a subagent: the tree, the two modes and the way back.

A subagent is an agent. It runs the same tools, the same model and its own
profile prompt; what makes it a *sub*agent is only who started it and where its
message goes. This module is what starts it, keeps it, and carries the messages
in both directions.

**Sync and async are two ways of waiting, not two kinds of agent.**

- ``sync``: the tool does not return until the subagent finishes, and the tool
  result *is* the subagent's message. The parent reads it as a result, which is
  what it wants when the next step depends on it.
- ``async``: the tool returns as soon as the node exists, and the message is
  delivered later as a **user message** to whoever summoned it. That is the only
  honest shape for a callback inside an agent run: a tool result cannot arrive
  after its turn closed, but a message can be enqueued, and the parent model reads
  it as a new instruction with the content in front of it.

Both modes are the *same* run, started as a task and tracked; the mode only decides
whether the caller waits. That is what makes the other two verbs possible:

- ``detach``: a sync caller stops waiting, the node keeps working and its answer
  arrives later as an async one. This is the "send it to the background" of the
  UI, and it costs one flag because the run was never in the caller's stack.
- ``kill``: the task is cancelled and the node is marked ``cancelled``.

**Resuming is not respawning.** A node keeps its transcript, so a second call with
its id continues the same conversation instead of starting from nothing: the new
prompt is appended to what the node already did. A call *without* an id always
starts a new node, even for the same profile — the id is the execution, the name is
the kind of agent.

**A child cannot outlive its parent.** When a node ends, whatever was still running
under it is stopped: its answer has nobody to reach, and keeping it alive would
leave a branch hanging from a parent that no longer exists.

**Messages travel by the channel of the sender's profile.** A parent steers its
child (an instruction); a child, if it declared a bidirectional channel, sends
*notices* to its parent and to the siblings it named. A notice is information, and
the prompt says so: the receiver decides whether it deserves an answer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic_ai.exceptions import RunCancelled, UserError
from pydantic_ai.usage import UsageLimits

from src.engine.agents.profile.models.agent_not_found import AgentNotFound
from src.engine.agents.step_budget import StepBudget
from src.engine.agents.tree.models.agent_message import AgentMessage
from src.engine.agents.tree.models.agent_node import AgentNode
from src.engine.agents.tree.models.node_status import NodeStatus
from src.engine.agents.tree.models.summon_error import SummonError
from src.engine.sessions.recovery import recover_history
from src.engine.tooling.events import CURRENT_NODE_ID

if TYPE_CHECKING:
    from pydantic_ai import Agent

    from src.engine.agents.profile.models.agent_profile import AgentProfile
    from src.engine.agents.profile.registry import AgentRegistry
    from src.engine.agents.tree.store import AgentTreeStore
    from src.engine.agents.tree.tree import AgentTree

SummonMode = Literal['sync', 'async']
"""How the caller waits: blocked on the result, or free until it arrives."""

DEFAULT_MAX_DEPTH = 3
"""How deep summoning may nest.

Depth 1 is the normal case: the agent summons a subagent. Depth 2 means a subagent
summoned another, which is allowed — it is the same tool — but it is also the
shape that runs away when nobody is watching, so the chain is capped. The cap is
about the chain, not the branch: an agent may summon ten subagents in a row.
"""


@dataclass(frozen=True, slots=True)
class SigilDeps:
    """What a run knows about the node it belongs to.

    It exists for the tools of the tree — ``summon``, ``send_message``,
    ``kill_subagent`` and the prompt blocks that list the neighbours. The root's
    run carries ``None``, so a tool resolves the root from the tree in effect
    instead of from a value the app would have to keep in step with `/load`.
    """

    node_id: str | None = None


OnChange = Callable[[AgentNode], None]
"""A node was born or changed state. The UI refreshes the tree."""

RootRun = Callable[[], Any | None]
"""The run of the agent at the root, while it is alive, or ``None``.

It is a callable and not a value: the app owns the turn, the turn owns the run, and
both change between the moment the summoner is built and the moment a message
arrives. ``None`` — or a run that already ended — means the message has to wait for
the next real turn.
"""

OnDelivered = Callable[[AgentNode], None]
"""A message was handed to a live run. The UI says so in the chat."""

OnIdle = Callable[[], None]
"""An answer is waiting and no turn is running to read it.

The app decides what that means: it may start a turn on its own, or leave the
answer for the next one. The engine does not start turns; it only says that there
is something to read, because a turn is a user-facing act and the app owns it.
"""

Builder = Callable[['AgentProfile'], 'Agent[Any]']
"""Builds the agent object of one profile. The tree node is not its concern."""


class SubagentSummoner:
    """Starts subagents, records them in the tree and carries the messages."""

    def __init__(
        self,
        *,
        trees: AgentTreeStore,
        registry: AgentRegistry,
        builder: Callable[[], Builder],
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_steps: int = 0,
        model: Callable[[], str] | None = None,
        model_settings: Callable[[], dict[str, Any] | None] | None = None,
        on_change: OnChange | None = None,
        root_run: RootRun | None = None,
        on_delivered: OnDelivered | None = None,
        on_idle: OnIdle | None = None,
    ) -> None:
        self._trees = trees
        self._registry = registry
        # The builder is resolved per summon and not held: the factory that builds
        # agents needs this summoner to install the `summon` tool, so at the moment
        # this object is created the factory does not exist yet.
        self._builder = builder
        self._max_depth = max_depth
        self._max_steps = max_steps
        self._model = model
        self._model_settings = model_settings
        self._on_change = on_change
        self._root_run = root_run
        self._on_delivered = on_delivered
        self._on_idle = on_idle
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._detached: dict[str, asyncio.Event] = {}

    # --- who listens -------------------------------------------------------

    def listen(
        self,
        *,
        on_change: OnChange | None = None,
        root_run: RootRun | None = None,
        on_delivered: OnDelivered | None = None,
        on_idle: OnIdle | None = None,
    ) -> None:
        """Attaches the listeners of the UI, once it exists.

        The engine is built before the app, and the app is what paints the tree and
        holds the run a late message has to reach, so this is a setter and not a
        constructor argument.
        """
        self._on_change = on_change
        self._root_run = root_run
        self._on_delivered = on_delivered
        self._on_idle = on_idle

    # --- what the tools ask for --------------------------------------------

    def tree(self) -> AgentTree:
        """The tree in effect: read by the tools and by the prompt blocks."""
        return self._trees.active

    def resolve(self, ref: str) -> AgentNode:
        """The node a reference names, accepting a unique prefix of its id.

        Agents never see the full 32-character id in the places where they read one
        — the row of `list_agents`, the block of the agents around them, the note in
        a status line — and copying eight characters is what a person does too. The
        prefix is only accepted when it is unambiguous, because guessing between two
        executions of the same subagent is how a message reaches the wrong one.

        Raises:
            SummonError: Nothing matches, or more than one node does.
        """
        text = ref.strip().lstrip('#')
        tree = self._trees.active
        exact = tree.node(text)
        if exact is not None:
            return exact
        matches = [node for node in tree.nodes() if text and node.id.startswith(text)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            listed = ', '.join(f'#{node.short_id} ({node.name})' for node in matches[:5])
            raise SummonError(f'{ref!r} matches more than one agent: {listed}. Use a longer id.')
        raise SummonError(
            f'No agent with id {ref!r} in this session. The id is the one `summon` returned '
            '— or its first characters — not the name of the profile.'
        )

    def is_live(self, node: AgentNode) -> bool:
        """Whether a node has a run right now, which is what makes it reachable.

        A node's status is ``running`` from the moment it starts until it ends, and
        the root's is ``running`` for the whole session: neither says whether there
        is a run waiting to read a message. This does.
        """
        return self._run_of(node) is not None

    async def summon(
        self,
        *,
        parent_id: str | None,
        name: str = '',
        task: str,
        mode: SummonMode = 'sync',
        agent_id: str | None = None,
    ) -> str:
        """Starts — or continues — a subagent and returns what the caller reads.

        For ``sync`` that is the subagent's own message; for ``async`` it is an
        acknowledgement, and the message arrives later. Both carry the node id, so
        the parent can talk to that execution later: send it a message, kill it, or
        resume it when it ran out of steps.

        Args:
            parent_id: The node that is calling. ``None`` means the root.
            name: Profile to start. Ignored when ``agent_id`` is given: a resume is
                identified by the execution, not by the kind of agent.
            task: What it has to do, or the new prompt of a resumed node.
            mode: Whether to wait for the answer.
            agent_id: Node to resume instead of starting a new one.

        Raises:
            SummonError: The subagent cannot be started: unknown or not a
                subagent, no task, too deep, or the node cannot be resumed.
        """
        tree = self._trees.active
        caller = tree.node(parent_id) or tree.root
        if caller is None:
            raise SummonError('There is no agent tree for this session: the run has no parent to hang a subagent from.')
        if not task.strip():
            raise SummonError(
                '`task` is empty: a subagent cannot see this conversation, so its task has to say what it needs.'
            )
        if agent_id:
            return await self._resume(caller, agent_id, task.strip(), mode)
        return await self._spawn(caller, name, task.strip(), mode)

    # --- a new node --------------------------------------------------------

    async def _spawn(self, caller: AgentNode, name: str, task: str, mode: SummonMode) -> str:
        tree = self._trees.active
        if not name.strip():
            raise SummonError(
                '`agent` is required to start a subagent. Pass the name of a subagent from your '
                'instructions, or `agent_id` to continue one that already ran.'
            )
        profile = self._profile(name)
        if profile.name == caller.name:
            raise SummonError(
                f'`{profile.name}` is the agent that is calling: a subagent cannot summon itself. '
                'Do this part yourself, or summon another profile.'
            )
        if caller.depth + 1 > self._max_depth:
            raise SummonError(
                f'Subagents are nested too deep ({self._max_depth} levels): `{profile.name}` '
                f'would run at level {caller.depth + 1}. Do this part yourself, or have the '
                'current agent report back to its parent and let the chain close.'
            )
        node = tree.add(
            parent_id=caller.id,
            name=profile.name,
            role=profile.role,
            task=task,
            mode=mode,
            model=self._model() if self._model is not None else '',
        )
        node.channel = profile.channel
        node.max_steps = profile.max_steps
        return await self._launch(node, profile, task, mode)

    # --- the same node, again ----------------------------------------------

    async def _resume(self, caller: AgentNode, agent_id: str, task: str, mode: SummonMode) -> str:
        """Continues a node with a new prompt, on top of its own transcript."""
        tree = self._trees.active
        node = self.resolve(agent_id)
        if node.is_root:
            raise SummonError('That id is the agent of the session, not a subagent: send it a message instead.')
        if not tree.can_reach(caller.id, node.id):
            raise SummonError(
                f'#{node.short_id} was not summoned by you or by an agent above you: you cannot resume it.'
            )
        if not node.status.finished:
            raise SummonError(
                f'#{node.short_id} is still working. Use `send_message` to steer it, or `kill_subagent` '
                'to stop it before resuming with something else.'
            )
        profile = self._profile(node.name)
        # A node that was killed or cancelled mid-tool leaves a call with no
        # answer; a prompt on top of that is refused for good. The tail is closed
        # out here, before the history is handed back to the agent.
        node.messages = recover_history(node.messages).messages
        node.mode = mode
        node.max_steps = profile.max_steps
        node.channel = profile.channel
        return await self._launch(node, profile, task, mode)

    # --- running one node --------------------------------------------------

    async def _launch(self, node: AgentNode, profile: AgentProfile, task: str, mode: SummonMode) -> str:
        """Starts the run as a task, then decides whether to wait for it."""
        node.start()
        self._notify(node)
        token = CURRENT_NODE_ID.set(node.id)
        try:
            handle = asyncio.create_task(self._execute(node, profile, task), name=f'subagent-{node.short_id}')
        finally:
            CURRENT_NODE_ID.reset(token)
        self._tasks[node.id] = handle
        handle.add_done_callback(lambda _finished, node_id=node.id: self._tasks.pop(node_id, None))
        if mode == 'async':
            return (
                f'#{node.short_id} ({profile.label}) is working in the background. The answer arrives as a new '
                'user message when it finishes — continue with something else, or stop and wait. '
                'Do not summon the same task again.'
            )
        return await self._wait(node)

    async def _wait(self, node: AgentNode) -> str:
        """Waits for a sync node, unless somebody sends it to the background."""
        handle = self._tasks.get(node.id)
        if handle is None:
            return f'#{node.short_id} finished before it could be awaited.'
        detached = asyncio.Event()
        self._detached[node.id] = detached
        waiter = asyncio.create_task(detached.wait())
        try:
            done, _ = await asyncio.wait({handle, waiter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiter.cancel()
            self._detached.pop(node.id, None)
        if waiter in done:
            return (
                f'#{node.short_id} ({node.label}) was moved to the background. It keeps working and its '
                'answer will arrive as a new user message.'
            )
        return self._result(node, handle)

    def _result(self, node: AgentNode, handle: asyncio.Task[Any]) -> str:
        """What a sync caller gets: the message, or why there is none."""
        if handle.cancelled():
            # Killed from outside while this call was waiting. Not an exception in
            # the parent's turn: killing one subagent must not cancel its caller.
            return f'[{node.label} #{node.short_id} was stopped before it answered.]'
        error = handle.exception()
        if error is not None:
            raise SummonError(f'#{node.short_id} ({node.name}) failed: {type(error).__name__}: {error}') from error
        message = handle.result() or ''
        if node.status is NodeStatus.FAILED:
            raise SummonError(_envelope(node, message or (node.error or 'failed')))
        return _envelope(node, message)

    async def _execute(self, node: AgentNode, profile: AgentProfile, task: str) -> str:
        """Runs the node to its end and returns the message it sends back."""
        agent = self._builder()(profile)
        budget = StepBudget(
            max_steps=node.max_steps or self._max_steps or 1,
            role='subagent',
        )
        run: Any = None
        message = ''
        try:
            async with agent.iter(
                task,
                deps=SigilDeps(node_id=node.id),
                message_history=list(node.messages) or None,
                usage_limits=UsageLimits(request_limit=None),
                capabilities=[budget],
                model_settings=self._model_settings() if self._model_settings is not None else None,
            ) as run:
                node.run = run
                async for _step in run:
                    # One transcript snapshot per step, announced to the UI: it is
                    # what a live view of a running subagent paints, and it costs a
                    # list copy against a model request.
                    node.messages = list(run.all_messages())
                    self._notify(node)
            node.messages = list(run.all_messages())
            message = _output(run)
            node.finish(message, exhausted=budget.exhausted)
        except RunCancelled:
            node.cancel()
        except asyncio.CancelledError:
            if node.status is not NodeStatus.CANCELLED:
                node.cancel()
            raise
        except Exception as exc:  # noqa: BLE001 - a node that dies is not a dead session
            node.fail(f'{type(exc).__name__}: {exc}')
            message = f'[{profile.label} failed: {node.error}]'
        finally:
            if run is not None and not node.messages:
                node.messages = list(run.all_messages())
            node.run = None
            # A branch cannot hang from a parent that stopped: whatever is still
            # running under this node is stopped with it.
            self._cascade_stop(node)
            self._notify(node)
        # The mode is read at the end, not at the start: it may have been flipped
        # to `async` while the node worked, which is exactly what "move it to the
        # background" means.
        if node.mode == 'async' and node.status in (NodeStatus.DONE, NodeStatus.FAILED):
            self._deliver(node, message)
        return message

    # --- stopping ----------------------------------------------------------

    def detach(self, node_id: str) -> str:
        """Sends a running node to the background: nobody waits for it anymore.

        The run is untouched — it was never in the caller's stack, only awaited by
        it. Flipping the mode is what changes where the answer goes; waking the
        waiter is what frees the caller.
        """
        node = self.resolve(node_id)
        if node.status.finished:
            raise SummonError(f'#{node.short_id} already finished: there is nothing to move to the background.')
        if node.mode == 'async':
            return f'#{node.short_id} is already in the background.'
        node.mode = 'async'
        event = self._detached.get(node_id)
        if event is not None:
            event.set()
        self._notify(node)
        return f'#{node.short_id} ({node.label}) moved to the background: its answer will arrive as a message.'

    def kill(self, actor_id: str | None, node_id: str) -> str:
        """Stops a node and everything under it.

        Killing is what makes a background subagent usable: it outlives the turn
        that started it, so without this the only way to stop one is to quit.
        """
        tree = self._trees.active
        node = self.resolve(node_id)
        if node.is_root:
            raise SummonError('That is the agent of the session, not a subagent: stop the turn instead.')
        actor = tree.node(actor_id) or tree.root
        if actor is not None and not tree.can_reach(actor.id, node.id):
            raise SummonError(f'#{node.short_id} was not summoned by you or by an agent above you: you cannot stop it.')
        if node.status.finished:
            return f'#{node.short_id} ({node.label}) had already finished.'
        self._stop(node)
        return f'#{node.short_id} ({node.label}) stopped, along with everything it had summoned.'

    def _stop(self, node: AgentNode) -> None:
        """Cancels the node and its descendants, deepest first."""
        for child in reversed(self._trees.active.descendants(node.id)):
            self._cancel(child)
        self._cancel(node)

    def _cancel(self, node: AgentNode) -> None:
        if node.status is NodeStatus.CANCELLED:
            return
        node.cancel()
        handle = self._tasks.get(node.id)
        if handle is not None:
            handle.cancel()
        self._notify(node)

    def _cascade_stop(self, node: AgentNode) -> None:
        """Stops what was still running under a node that just ended."""
        for child in self._trees.active.descendants(node.id):
            if not child.status.finished:
                self._cancel(child)

    def cancel_all(self) -> None:
        """Stops every asynchronous node. Called when the turn or the session ends.

        The state is written here and not only from the node's own handler: a task
        cancelled before its coroutine ever ran never reaches that handler, and the
        tree would keep a node saying it is still working.
        """
        tree = self._trees.active
        for node_id, handle in tuple(self._tasks.items()):
            node = tree.node(node_id)
            if node is not None and not node.status.finished:
                node.cancel()
                self._notify(node)
            handle.cancel()
        self._tasks.clear()

    def running(self) -> int:
        return len(self._tasks)

    # --- messages between agents -------------------------------------------

    def send_message(self, *, sender_id: str | None, target_id: str, text: str) -> str:
        """Steer a child directly, or add a notice to the receiver's context table.

        Both paths require a live receiver. Final async reports retain their own
        delivery mechanism; they are not intermediate notifications.
        """
        body = text.strip()
        if not body:
            raise SummonError('`message` is empty: say what you want to tell it.')
        tree = self._trees.active
        sender = tree.root if sender_id is None else tree.node(sender_id)
        if sender is None:
            raise SummonError('There is no agent tree for this session.')
        target = self.resolve(target_id)
        if target.id == sender.id:
            raise SummonError('You are that agent: there is nothing to send.')

        kind: Literal['instruction', 'notice'] = self._allowed(tree, sender, target)
        if not self.is_live(target):
            raise SummonError(self._unreachable(tree, sender, target, body))

        if kind == 'notice':
            notice = tree.notifications.publish(sender, target.id, body)
            self._notify(target)
            return f'Notification {notice.id} delivered to {target.label} #{target.short_id}; pending ACK.'
        message = AgentMessage(sender_id=sender.id, sender_name=sender.name, kind=kind, text=body)
        if not _enqueue(self._run_of(target), message.render()):
            raise SummonError(self._unreachable(tree, sender, target, body))
        self._announce_delivery(target)
        return f'Instruction queued for {target.label} #{target.short_id} as steering.'

    def ack_notifications(self, *, receiver_id: str | None, notification_ids: list[str]) -> str:
        """Confirm receipt without removing content, changing order, or resolving it."""
        tree = self._trees.active
        receiver = tree.root if receiver_id is None else tree.node(receiver_id)
        if receiver is None:
            raise SummonError('There is no receiving execution in this session.')
        try:
            tree.notifications.acknowledge(receiver.id, notification_ids)
        except ValueError as error:
            raise SummonError(str(error)) from error
        self._notify(receiver)
        return f'ACK recorded for {", ".join(dict.fromkeys(notification_ids))}. Receipt only; notifications remain visible.'

    def _allowed(self, tree: AgentTree, sender: AgentNode, target: AgentNode) -> Literal['instruction', 'notice']:
        """Which message this is, or a refusal explaining why there is none.

        Only the *relationship* is decided here. Whether the receiver can read it
        now is a separate question, answered by ``_unreachable``.
        """
        if target.parent_id == sender.id:
            # Parent to child: this is steering, always allowed.
            return 'instruction'
        if target.id == sender.parent_id:
            if not sender.channel.may_message_parent():
                raise SummonError(
                    f'Your channel does not allow talking to your parent ({sender.channel.describe()}). '
                    'Say what you need in your final message instead: it reaches them that way.'
                )
            return 'notice'
        if target.parent_id == sender.parent_id:
            if not sender.channel.may_message_sibling(target.name):
                raise SummonError(
                    f'You may not message `{target.name}`: your channel lists '
                    f'{", ".join(sender.channel.to_siblings) or "no siblings"}. '
                    'If it is needed, say so to your parent.'
                )
            return 'notice'
        raise SummonError(
            'You can only message your direct children, your parent, and the siblings your channel names. '
            f'{target.label} #{target.short_id} is none of those.'
        )

    def _unreachable(self, tree: AgentTree, sender: AgentNode, target: AgentNode, body: str) -> str:
        """Why the receiver cannot read a message, and what the sender can do.

        The advice depends on who the sender is, because the fix does: a parent can
        bring an execution back with the message as its task; a child cannot bring
        its parent back at all; a sibling cannot resume anyone.
        """
        name = f'{target.label} #{target.short_id}'
        if target.is_root:
            return (
                f'Your parent {name} is not in a turn right now, so nothing can read that message. '
                'You cannot start it: put this in your final message instead, where it reaches '
                'them with the rest of your report.'
            )
        if not target.status.finished:
            # Running, but its run object is not there yet. It is a moment, not a
            # state: saying "resume it" here would send the parent into a loop.
            return f'{name} is starting up and cannot read a message yet: try again in a moment.'
        if target.parent_id == sender.id:
            return (
                f'{name} is not running: it finished, so nothing can read that message now. '
                'You are its parent, so resume it with the message as the new task:\n'
                f'    summon(agent_id="{target.id}", task={body!r})\n'
                'It keeps its transcript, so it reads your message on top of everything it '
                'already did. Do this only if it needs to act on it — otherwise continue '
                'yourself.'
            )
        if target.parent_id == sender.parent_id:
            return (
                f'{name} has already finished, and a sibling cannot resume it. '
                'Ask your parent to, or put it in your final message.'
            )
        return (
            f'{name} is not running, and it is above you in the tree: only an agent that summoned '
            'it can resume it. Put this in your final message instead.'
        )

    def waiting(self) -> list[AgentNode]:
        """Finished nodes whose answer nobody has read yet, in tree order."""
        return [node for node in self._trees.active.nodes() if not node.delivered]

    def take_messages(self) -> list[str]:
        """The answers waiting for a turn, and marks them read.

        It reads the tree and not a list held in memory: the flag travels with the
        session, so closing the app between an answer and the next turn loses
        nothing.
        """
        waiting = self.waiting()
        messages: list[str] = []
        for node in waiting:
            node.delivered = True
            messages.append(_envelope(node, node.result or ''))
        if waiting:
            self._notify(waiting[-1])
        return messages

    def drain_pending(self) -> list[str]:
        """Same queue, older name: the turn machinery already calls this."""
        return self.take_messages()

    @property
    def pending(self) -> tuple[str, ...]:
        """The answers waiting, without consuming them."""
        return tuple(_envelope(node, node.result or '') for node in self.waiting())

    def _wake(self) -> None:
        """Tells the app an answer is waiting with nobody to read it. Never raises."""
        if self._on_idle is None:
            return
        try:
            self._on_idle()
        except Exception:  # noqa: BLE001, S110 - the wake is not the delivery
            pass

    # --- the way back ------------------------------------------------------

    def _deliver(self, node: AgentNode, message: str) -> None:
        """Sends the node's message to whoever summoned it.

        The parent is the node that called `summon`; when that node has already
        finished, the message climbs to the nearest ancestor still able to read it
        and, failing all of them, to the root.
        """
        node.delivered = False
        text = _envelope(node, message)
        if self._hand(node, text):
            node.delivered = True
            self._notify(node)
            return
        # Nobody took it. The flag is what makes the message survive the process:
        # the tree is saved with the session, and the next open finds it waiting.
        self._notify(node)
        self._wake()

    def _hand(self, node: AgentNode, text: str) -> bool:
        """Offers the message to the nearest live run. ``False`` when none took it."""
        tree = self._trees.active
        parent = tree.parent(node.id)
        while parent is not None:
            run = self._run_of(parent)
            if run is not None and _enqueue(run, text):
                self._announce_delivery(node)
                return True
            parent = tree.parent(parent.id)
        return False

    def _run_of(self, node: AgentNode) -> Any | None:
        """The live run of a node: its own, or the root's turn when it is the root."""
        if not node.is_root:
            return node.run
        if self._root_run is None:
            return None
        try:
            return self._root_run()
        except Exception:  # noqa: BLE001 - a broken provider is a missing run
            return None

    def _announce_delivery(self, node: AgentNode) -> None:
        """Tells the UI that a message landed in a running turn. Never raises."""
        if self._on_delivered is None:
            return
        try:
            self._on_delivered(node)
        except Exception:  # noqa: BLE001, S110 - the notice is not the delivery
            pass

    # --- notifications -----------------------------------------------------

    def _profile(self, name: str) -> AgentProfile:
        """The profile a summon names, mapped to a refusal the model can read."""
        try:
            return self._registry.require_subagent(name)
        except AgentNotFound as exc:
            raise SummonError(str(exc)) from exc

    def _notify(self, node: AgentNode) -> None:
        """Tells the UI a node was born or changed. Never raises into the run."""
        if self._on_change is None:
            return
        try:
            self._on_change(node)
        except Exception:  # noqa: BLE001, S110 - the tree is not the run
            pass


def _enqueue(run: Any, text: str) -> bool:
    """Injects a message into a live run. ``False`` when that run is closed."""
    try:
        run.enqueue(text)
    except UserError:
        # The run ended between the check and the call. The caller falls through
        # to the next way of delivering it.
        return False
    except Exception:  # noqa: BLE001 - enqueue can refuse content; never crash a node
        return False
    return True


def _output(run: Any) -> str:
    """The final message of a run, as the parent reads it."""
    result = getattr(run, 'result', None)
    output = getattr(result, 'output', None)
    if output is None:
        return '(the subagent finished without a message)'
    text = output if isinstance(output, str) else str(output)
    return text.strip() or '(the subagent finished without a message)'


def _envelope(node: AgentNode, message: str) -> str:
    """What the parent reads: the message under a header that names the node.

    The header is not decoration. It carries the id, which is what lets the parent
    act on that execution later — message it, kill it, or resume it when it ran out
    of steps — and it says how the node ended, because a report from an agent that
    used up its budget has to be read differently from a finished one.
    """
    state = {
        NodeStatus.DONE: 'out of steps' if node.budget_exhausted else 'finished',
        NodeStatus.FAILED: 'failed',
        NodeStatus.CANCELLED: 'was cancelled',
        NodeStatus.RUNNING: 'is still running',
    }[node.status]
    header = f'[subagent {node.label} ({node.name} #{node.short_id}, {node.mode}) {state}]'
    if node.budget_exhausted:
        header += (
            '\n[it used all its steps: what follows is what it managed to establish. '
            f'Resume it with summon(agent_id="{node.id}") to have it continue.]'
        )
    return f'{header}\n{message}'


__all__ = [
    'DEFAULT_MAX_DEPTH',
    'SigilDeps',
    'SubagentSummoner',
    'SummonMode',
]
