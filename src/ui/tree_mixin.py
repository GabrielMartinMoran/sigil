"""The tree in the app: the view of a subagent, the way back, and the messages.

Three responsibilities, all of them about the same object — the session's tree:

- **Navigation.** `alt+t` opens the tree; choosing a node opens that subagent in
  place of the chat, and `Esc` returns to the parent. The composer is disabled
  while a node is open: a subagent's conversation is driven by the task it was
  given, and a message typed there would be a message nobody reads.
- **Painting.** The node view is repainted when its node moves on, and the tree
  screen is refreshed while it is open, so a subagent that finishes does not go on
  saying it is running.
- **The way back for async subagents.** A message that arrives while a turn is
  running goes into that run, as a user message the model reads next; one that
  arrives while nothing runs is queued in the summoner and prepended to the next
  turn. It never starts a turn by itself: spending a model request nobody asked
  for is worse than a message waiting for a second.

The node view replaces the chat the same way the debug view does; the two are
mutually exclusive because they occupy the same space.
"""

from __future__ import annotations

from typing import Any

from textual.app import ScreenStackError
from textual.css.query import NoMatches

from src.engine.agents.tree.models.agent_node import AgentNode
from src.ui.messages import TreeChanged
from src.ui.widgets.agent_tree_screen import AgentTreeScreen, TreeAction
from src.ui.widgets.agent_view import AgentView
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput

NO_TREE_NOTICE = 'This session has no agents yet: send a message and the tree starts with it.'
"""What `alt+t` says when the app was built without a tree (headless runs)."""


class TreeMixin:
    """The tree view, the node view and the delivery of late messages.

    It expects the app to define ``_summoner``, ``_agent_tree``, ``_turn_active``
    and ``_current_run``.
    """

    _summoner: Any
    _agent_tree: Any
    _turn_active: bool

    # --- the tree ----------------------------------------------------------

    def agent_tree(self) -> Any:
        """The tree in effect, or ``None`` when the app has none."""
        return self._agent_tree

    def tree_nodes(self) -> list[AgentNode]:
        tree = self.agent_tree()
        return [] if tree is None else tree.active.nodes()

    def node(self, node_id: str | None) -> AgentNode | None:
        tree = self.agent_tree()
        return None if tree is None or not node_id else tree.active.node(node_id)

    async def open_tree(self) -> None:
        """`alt+t`: the list of agents of the session, and what to do with one."""
        chat = self.query_one(ChatLog)
        tree = self.agent_tree()
        if tree is None:
            await chat.add_notice(NO_TREE_NOTICE, style='yellow')
            return
        nodes = self.tree_nodes()
        if len(nodes) <= 1:
            # Only the root: the tree exists, it is just not a tree yet. Saying so
            # is better than opening a box with one row the user already knows.
            await chat.add_notice('No subagents in this session yet.', style='yellow')
            return
        chosen = await self.push_screen_wait(AgentTreeScreen(nodes, viewed=self.viewed_node_id()))
        if chosen is None:
            return
        await self.run_tree_action(chosen)

    async def run_tree_action(self, action: TreeAction) -> None:
        """Carries out what the user picked in the tree."""
        if action.kind == 'open':
            await self.goto_node(action.node_id)
            return
        chat = self.query_one(ChatLog)
        if action.kind == 'detach':
            await self.send_to_background(action.node_id, chat)
            return
        await self.stop_node(action.node_id, chat)

    async def send_to_background(self, node_id: str, chat: ChatLog) -> bool:
        """`b`: the node keeps working and answers later, instead of blocking."""
        summoner = getattr(self, '_summoner', None)
        if summoner is None:
            await chat.add_notice('This app has no subagents.', style='yellow')
            return False
        try:
            message = summoner.detach(node_id)
        except Exception as exc:  # noqa: BLE001 - a refusal is a line, not a crash
            await chat.add_notice(str(exc), style='yellow')
            return False
        await chat.add_notice(message, style='cyan')
        return True

    async def stop_node(self, node_id: str, chat: ChatLog) -> bool:
        """`k`: the node is stopped, along with everything it summoned."""
        summoner = getattr(self, '_summoner', None)
        if summoner is None:
            await chat.add_notice('This app has no subagents.', style='yellow')
            return False
        try:
            message = summoner.kill(None, node_id)
        except Exception as exc:  # noqa: BLE001 - a refusal is a line, not a crash
            await chat.add_notice(str(exc), style='yellow')
            return False
        await chat.add_notice(message, style='cyan')
        return True

    async def goto_node(self, node_id: str) -> None:
        """Opens ``node_id``: the subagent view, or the chat for the root."""
        node = self.node(node_id)
        if node is None:
            return
        if node.is_root:
            self.show_chat()
            return
        await self.open_subagent(node)

    async def open_subagent(self, node: AgentNode) -> None:
        """Replaces the chat with the node and locks the composer."""
        view = self.query_one(AgentView)
        chat = self.query_one(ChatLog)
        parent = self.node(node.parent_id)
        # The debug view is the other thing that replaces the chat, and closing it
        # gives the chat back: it has to happen *before* the node takes the space.
        self.set_debug_visible(False)
        self._viewed_node = node.id
        view.display = True
        chat.display = False
        await view.show(node, parent_label=_label(parent), has_parent=True)
        prompt = self.query_one(PromptInput)
        prompt.disabled = True
        self._release_prompt_focus()

    async def open_parent(self) -> None:
        """`Esc` inside a node: back to whoever summoned it."""
        node = self.node(self.viewed_node_id())
        if node is None:
            self.show_chat()
            return
        parent = self.node(node.parent_id)
        if parent is None or parent.is_root:
            self.show_chat()
            return
        await self.open_subagent(parent)

    def show_chat(self) -> bool:
        """Back to the conversation with the agent at the root.

        Synchronous on purpose: it is called from paths that cannot wait (a key, a
        session swap) and from paths that could, and a view toggle that lands one
        frame later than the one it replaces is a frame with two chats.
        """
        self._viewed_node = None
        if not self._restore_chat():
            return False
        self.query_one(ChatLog).anchor()
        return True

    def _restore_chat(self) -> bool:
        """Hides the node view and gives the composer back. ``False`` if not composed."""
        try:
            view = self.query_one(AgentView)
            chat = self.query_one(ChatLog)
            prompt = self.query_one(PromptInput)
        except NoMatches:
            return False
        view.display = False
        chat.display = True
        prompt.disabled = False
        prompt.focus()
        return True

    def _release_prompt_focus(self) -> None:
        """Takes the keys away from the composer while a node is open."""
        try:
            prompt = self.query_one(PromptInput)
        except NoMatches:
            return
        if prompt.has_focus:
            prompt.blur()

    def viewed_node_id(self) -> str | None:
        return getattr(self, '_viewed_node', None)

    @property
    def subagent_open(self) -> bool:
        """Whether the chat is replaced by a subagent."""
        try:
            return self.query_one(AgentView).display
        except NoMatches:
            return False

    # --- repainting --------------------------------------------------------

    def on_tree_changed(self, message: TreeChanged) -> None:
        """A node was born or moved on: repaint what shows it."""
        node = message.node
        if self.subagent_open and self.viewed_node_id() == node.id:
            self.run_worker(self._refresh_node_view(node))
        self._refresh_tree_screen()

    async def _refresh_node_view(self, node: AgentNode | None = None) -> None:
        try:
            view = self.query_one(AgentView)
        except NoMatches:
            return
        await view.refresh_node(node)

    def _refresh_tree_screen(self) -> None:
        """Keeps an open tree in step with the nodes it lists."""
        try:
            screen = self.screen
        except ScreenStackError:
            # Before the first screen is pushed there is nothing on screen to keep
            # in step, which is exactly the state a node is born in.
            return
        if isinstance(screen, AgentTreeScreen):
            screen.set_nodes(self.tree_nodes())

    async def repaint_agent_view(self) -> None:
        """Theme change: the node view carries the old palette."""
        try:
            view = self.query_one(AgentView)
        except NoMatches:
            return
        await view.repaint()

    # --- the way back for async subagents ----------------------------------

    def root_run(self) -> Any:
        """The run of the turn in flight, or ``None``.

        The summoner reads this when a subagent finishes: while it returns a live
        run, the message lands inside the turn the user is watching; when it does
        not, the message waits for the next one.
        """
        if not self._turn_active:
            return None
        return getattr(self, '_current_run', None)

    async def notify_subagent_message(self, node: AgentNode) -> None:
        """One line in the chat: a subagent answered and the turn will read it."""
        try:
            chat = self.query_one(ChatLog)
        except NoMatches:
            return
        await chat.add_notice(f'↩ {node.label} #{node.short_id} sent its message to the running turn.', style='dim')

    def announce_subagent_message(self, node: AgentNode) -> None:
        """Summoner listener: the message landed in the live turn."""
        self.run_worker(self.notify_subagent_message(node))

    def take_subagent_messages(self) -> list[str]:
        """What arrived while nothing was listening. The turn prepends it."""
        if self._summoner is None:
            return []
        return self._summoner.drain_pending()

    # --- lifecycle ---------------------------------------------------------

    def stop_subagents(self) -> None:
        """Stops every asynchronous node: the turn or the session is going away."""
        if self._summoner is not None:
            self._summoner.cancel_all()

    def rename_tree_root(self, name: str) -> None:
        """`/agent` moved: the root of the tree follows it."""
        tree = self.agent_tree()
        if tree is not None and name:
            tree.rename_root(name)

    def remember_tree(self, session: Any) -> None:
        """Points the tree store at the tree of the session just bound."""
        if self._agent_tree is None:
            return
        profile = self.active_agent()
        self._agent_tree.bind(session, root_name=profile.name if profile is not None else '')
        self._viewed_node = None
        # A node of the previous session is not a place in this one: the chat comes
        # back and the composer with it.
        if self.subagent_open:
            self._restore_chat()

    def announce_tree_change(self, node: AgentNode) -> None:
        """Listener the summoner calls: it only posts, the app paints."""
        self.post_message(TreeChanged(node))


def _label(node: AgentNode | None) -> str:
    """The parent's name, for the way-back line."""
    if node is None:
        return 'the session'
    return node.label if not node.is_root else f'the agent ({node.label})'


__all__ = ['NO_TREE_NOTICE', 'TreeMixin']
