"""The agents of a session, as rows.

The root is an execution like any other. That is the point of this module: before
the change the root's messages lived on `Session.messages` and a subagent's on
`AgentNode.messages`, so every reader of a transcript handled two shapes and every
writer had two code paths. A transcript is now always
`messages WHERE execution_id = ?`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING, Any

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.tree.models.agent_node import ROOT_TASK, AgentNode
from src.engine.agents.tree.models.node_status import NodeStatus
from src.engine.agents.tree.tree import AgentTree
from src.engine.storage.blobs import Blobs
from src.engine.storage.database import Database
from src.engine.storage.history import read_history, write_history
from src.engine.storage.notifications import load_notifications, save_notifications

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage


def root_id_for(session_id: str) -> str:
    """The execution id of a session's root.

    Derived, not random: the store and the tree have to agree on it before either
    has written a row, and a resumed write must not mint a second root.
    """
    return hashlib.sha256(f'root:{session_id}'.encode()).hexdigest()[:32]


def ensure_root(conn: sqlite3.Connection, session_id: str, name: str, started_at: float) -> str:
    """The root execution of a session, created if this is its first write.

    The name is kept in step on every write: `/agent` changes who answers, and the
    root is the execution that runs the session's profile. A root created by an
    empty session gets its name on the first save that knows one.
    """
    root_id = root_id_for(session_id)
    conn.execute(
        'INSERT OR IGNORE INTO agent_executions '
        '(id, session_id, parent_id, name, role, profile, depth, brief, status, started_at) '
        "VALUES (?, ?, NULL, ?, 'agent', ?, 0, ?, 'running', ?)",
        (root_id, session_id, name, name, ROOT_TASK, started_at),
    )
    if name:
        conn.execute(
            'UPDATE agent_executions SET name = ?, profile = ? WHERE id = ? AND (name != ? OR profile != ?)',
            (name, name, root_id, name, name),
        )
    return root_id


def _channel_json(node: AgentNode) -> str:
    return json.dumps(
        {
            'mode': str(node.channel.mode),
            'parent': node.channel.to_parent,
            'siblings': list(node.channel.to_siblings),
        },
        separators=(',', ':'),
    )


def save_executions(
    database: Database,
    blobs: Blobs,
    session_id: str,
    tree: AgentTree | None,
    root_messages: list[ModelMessage],
    *,
    conn: sqlite3.Connection,
    root_name: str = '',
) -> str:
    """Writes the executions of a session and their transcripts.

    ``root_messages`` is the session's history: it belongs to the root execution,
    not to the session row. Every other transcript comes from its node.
    """
    import time

    # The root's name is the root node's when there is a tree: the node is what
    # `/agent` renames, and the session's field is the same fact kept twice.
    tree_root = tree.root if tree is not None else None
    root_id = ensure_root(conn, session_id, (tree_root.name if tree_root else '') or root_name, time.time())

    if tree is not None:
        for node in tree.nodes():
            if node.id == root_id:
                continue
            conn.execute(
                'INSERT INTO agent_executions '
                '(id, session_id, parent_id, created_by_turn, name, role, profile, depth, brief, '
                'status, mode, model, result, error, started_at, finished_at, turns, delivered, '
                'channel, max_steps, budget_exhausted) '
                'VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
                'ON CONFLICT(id) DO UPDATE SET '
                'name = excluded.name, profile = excluded.profile, status = excluded.status, '
                'mode = excluded.mode, model = excluded.model, result = excluded.result, '
                'error = excluded.error, finished_at = excluded.finished_at, turns = excluded.turns, '
                'delivered = excluded.delivered, channel = excluded.channel, '
                'max_steps = excluded.max_steps, budget_exhausted = excluded.budget_exhausted',
                (
                    node.id,
                    session_id,
                    node.parent_id,
                    node.name,
                    str(node.role),
                    node.name,
                    node.depth,
                    node.task,
                    str(node.status),
                    node.mode,
                    node.model,
                    node.result,
                    node.error,
                    node.started_at,
                    node.finished_at,
                    node.runs,
                    int(node.delivered),
                    _channel_json(node),
                    node.max_steps,
                    int(node.budget_exhausted),
                ),
            )
            write_history(database, blobs, node.id, node.messages, conn=conn)

    if tree is not None:
        save_notifications(conn, session_id, tree.notifications)
    write_history(database, blobs, root_id, root_messages, conn=conn)
    return root_id


def load_tree(database: Database, blobs: Blobs, session_id: str) -> AgentTree | None:
    """The tree of a session, with every transcript, or ``None`` when it has none."""
    rows = database.query(
        'SELECT * FROM agent_executions WHERE session_id = ? ORDER BY depth, started_at',
        (session_id,),
    )
    if not rows:
        return None
    nodes: list[AgentNode] = []
    for row in rows:
        node = AgentNode(
            id=str(row['id']),
            name=str(row['name']),
            role=_role(row['role']),
            parent_id=None if row['parent_id'] is None else str(row['parent_id']),
            depth=int(row['depth']),
            task=str(row['brief'] or ''),
            status=_status(row['status']),
            mode=str(row['mode'] or 'sync'),
            model=str(row['model'] or ''),
            result=None if row['result'] is None else str(row['result']),
            error=None if row['error'] is None else str(row['error']),
            started_at=float(row['started_at'] or 0.0),
            finished_at=None if row['finished_at'] is None else float(row['finished_at']),
            runs=int(row['turns'] or 0),
            delivered=bool(row['delivered']),
            channel=_channel(row['channel']),
            max_steps=None if row['max_steps'] is None else int(row['max_steps']),
            budget_exhausted=bool(row['budget_exhausted']),
        )
        nodes.append(node)
    # A node whose parent was dropped on load is not reachable from the root; the
    # tree shows it at the end rather than throwing away a run that happened.
    nodes.sort(key=lambda node: (node.parent_id is not None, node.started_at))
    tree = AgentTree(nodes)
    tree.notifications = load_notifications(database, session_id)
    for node in tree.nodes():
        node.messages = read_history(database, blobs, node.id)
    return tree


def root_messages(database: Database, blobs: Blobs, session_id: str) -> list[ModelMessage]:
    """The transcript of the session's root execution."""
    return read_history(database, blobs, root_id_for(session_id))


def root_name(database: Database, session_id: str) -> str:
    """The profile the root execution runs, as stored."""
    value = database.value('SELECT profile FROM agent_executions WHERE id = ?', (root_id_for(session_id),), '')
    return str(value or '')


def _role(raw: Any) -> AgentRole:
    try:
        return AgentRole(str(raw))
    except ValueError:
        return AgentRole.SUBAGENT


def _status(raw: Any) -> NodeStatus:
    try:
        return NodeStatus(str(raw))
    except ValueError:
        return NodeStatus.DONE


def _channel(raw: Any):
    from src.engine.agents.profile.models.channel import UNIDIRECTIONAL, parse_channel

    if isinstance(raw, str) and raw:
        try:
            return parse_channel(json.loads(raw))
        except Exception:  # noqa: BLE001 - a stored record is not a declaration
            return UNIDIRECTIONAL
    return UNIDIRECTIONAL


__all__ = [
    'ensure_root',
    'load_tree',
    'root_id_for',
    'root_messages',
    'root_name',
    'save_executions',
]
