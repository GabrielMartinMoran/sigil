"""Projections: what the web client paints, computed from rows and values.

The server does not push state; it answers for it. This module is the one place
that turns the engine's objects into JSON, so a route stays a call and a
projection, and the shape the client depends on is defined once.

Two rules from the architecture page apply here:

- **Nothing is invented.** A field that cannot be computed from what exists is
  left out, not faked. The transcript, for instance, is rebuilt from the
  pydantic-ai messages; what the model said while thinking is there because the
  messages carry it.
- **Text is data.** A tool result travels as the string the tool produced, and the
  client decides how to paint it. The one exception is the `ok` flag, which the
  engine already knows and a client would have to re-derive.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import BinaryContent, ModelMessage

from src.engine.agents.tree.models.agent_node import AgentNode
from src.engine.agents.tree.tree import AgentTree
from src.engine.context.images import image_size
from src.engine.mcp.tool_models.server_status import ServerStatus
from src.engine.roadmap.models.todo_item import TodoItem
from src.engine.sessions.session_store import Session, SessionSummary
from src.engine.skills.loaded import loaded_skills
from src.engine.tooling.presentation import human_bytes
from src.engine.workspace.models.worktree import Workspace

MAX_ITEM_CHARS = 200_000
"""Ceiling of one projected text block.

A single tool result is already capped by the engine; this is the backstop for a
transcript read that would otherwise put a hundred megabytes in one JSON body.
"""


def _text(value: Any) -> str:
    """Text of a value, with the one exception that matters: bytes are never text.

    ``str(b'\\x89PNG')`` is an escaped repr, which is unreadable and, for a payload of
    a few hundred kilobytes, enormous. A byte string that reaches here is described by
    its size; the callers that know better (images, file parts) describe it earlier.
    """
    if value is None:
        return ''
    if isinstance(value, bytes):
        return f'<{human_bytes(len(value))} of binary data>'
    return str(value)


def _cut(text: str) -> str:
    if len(text) <= MAX_ITEM_CHARS:
        return text
    return text[:MAX_ITEM_CHARS] + '\n… (truncated)'


def workspace_view(workspace: Workspace, *, repository: str | None = None) -> dict[str, Any]:
    """The active workspace, as the header paints it.

    ``repository`` is the name of the git repository the workspace belongs to. It is
    passed in rather than derived here because a worktree does not know its siblings:
    only the repository does. With it, the client can say `sigil › better-debug`
    instead of `better-debug`, which is the difference between a worktree and an
    unrelated project.
    """
    worktree = workspace.worktree
    return {
        'root': str(workspace.root),
        'label': workspace.label,
        'branch': workspace.branch,
        'is_worktree': workspace.is_worktree,
        'is_main': worktree is not None and worktree.is_main,
        'is_detached': worktree.is_detached if worktree is not None else False,
        'is_locked': worktree.is_locked if worktree is not None else False,
        'repository': repository,
        'worktree': None
        if worktree is None
        else {
            'path': str(worktree.path),
            'name': worktree.name,
            'is_main': worktree.is_main,
            'head': worktree.head,
        },
    }


def repositories_view(
    repository_name: str | None,
    repository_root: str | None,
    workspaces: list[Workspace],
    *,
    active: str,
) -> list[dict[str, Any]]:
    """The picker's list, grouped by repository instead of flattened.

    A linked worktree is a workspace of its own — its own root, its own sessions, its
    own tools — and the engine treats it that way. What it is *not* is a different
    project: it shares the repository, the history and the remotes. A flat list makes
    eleven worktrees of one repo look like eleven repositories, which is the bug this
    projection fixes.

    Ordering inside a group is main first, then by label: the main worktree is the
    one people mean when they say "the project", so it is the one at the top.
    """
    groups: dict[str, dict[str, Any]] = {}
    for workspace in workspaces:
        # Grouped by the repository root, which is the same for every worktree of it.
        # A workspace outside git has no repository and becomes its own group.
        key = repository_root or str(workspace.root)
        group = groups.get(key)
        if group is None:
            group = {
                'name': repository_name or (workspace.root.name or str(workspace.root)),
                'root': key,
                'workspaces': [],
            }
            groups[key] = group
        group['workspaces'].append(
            {
                **workspace_view(workspace, repository=repository_name),
                'active': str(workspace.root) == active,
            }
        )

    for group in groups.values():
        group['workspaces'].sort(key=lambda item: (not item['is_main'], item['label']))

    return sorted(groups.values(), key=lambda group: group['name'].lower())


def session_view(session: Session) -> dict[str, Any]:
    """The open conversation: what it is called and what it runs on."""
    return {
        'id': session.id,
        'short_id': session.short_id,
        'title': session.title,
        'display_title': session.display_title,
        'workspace': session.cwd,
        'agent': session.agent,
        'created_at': session.created_at,
        'updated_at': session.updated_at,
        'messages': len(session.messages),
    }


def summary_view(
    summary: SessionSummary, *, repository: str | None = None, is_worktree: bool = False
) -> dict[str, Any]:
    """One row of the session list.

    ``repository`` and ``is_worktree`` let the row say where the conversation lives
    without the client having to join against the workspace list: `sigil › better-debug`
    reads as "a worktree of sigil", and `sigil` alone reads as the main checkout.
    """
    return {
        'id': summary.id,
        'short_id': summary.short_id,
        'title': summary.title,
        'display_title': summary.display_title,
        'preview': summary.preview,
        'workspace': summary.workspace,
        'profile': summary.profile,
        'updated_at': summary.updated_at,
        'repository': repository,
        'is_worktree': is_worktree,
    }


def node_view(node: AgentNode, *, include_messages: bool = False) -> dict[str, Any]:
    """One execution of the tree."""
    data: dict[str, Any] = {
        'id': node.id,
        'short_id': node.short_id,
        'name': node.name,
        'label': node.label,
        'role': str(node.role),
        'status': str(node.status),
        'parent_id': node.parent_id,
        'depth': node.depth,
        'task': node.task,
        'result': node.result,
        'error': node.error,
        'model': node.model,
        'mode': node.mode,
        'runs': node.runs,
        'elapsed': round(node.elapsed, 1),
        'delivered': node.delivered,
        'budget_exhausted': node.budget_exhausted,
    }
    if include_messages:
        data['transcript'] = transcript(node.messages)
    return data


def tree_view(tree: AgentTree) -> dict[str, Any]:
    """The whole tree, in tree order, ready to render as a list.

    A list and not nested children: the client draws the indentation from
    ``depth``, and a flat array is what a locator can address.
    """
    return {
        'root_id': tree.root_id,
        'nodes': [node_view(node) for node in tree.nodes()],
    }


def roadmap_view(items: list[TodoItem]) -> list[dict[str, Any]]:
    """The plan of one execution."""
    return [
        {
            'id': item.id,
            'title': item.title,
            'status': item.status,
            'notes': item.notes,
            'updated_at': item.updated_at,
        }
        for item in items
    ]


def mcp_view(statuses: list[ServerStatus]) -> dict[str, Any]:
    """MCP servers as data: the client decides how a row looks."""
    return {
        'servers': [
            {
                'name': status.name,
                'transport': status.transport,
                'enabled': status.enabled,
                'prefix': status.prefix,
                'tools': list(status.tools),
                'error': status.error,
                'source': status.source,
            }
            for status in statuses
        ]
    }


def skills_view(messages: list[ModelMessage]) -> list[str]:
    """The skills the transcript says were loaded.

    Derived, never stored: the set is a fact of the conversation, and a client that
    remembered it would drift the moment a turn loaded another one.
    """
    return list(loaded_skills(messages))


def transcript(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """The conversation as a flat list of items a client can paint.

    The shapes are the pydantic-ai part kinds, not a new vocabulary: ``user``,
    ``assistant``, ``thinking``, ``tool_call``, ``tool_result`` and ``error``.
    A tool call and its result travel as two items with the same ``call_id``,
    because that is how they exist in the history — the pairing is the client's,
    and doing it here would lose the case where a result never arrived.

    **Binary payloads never travel.** An image is described by what it is —
    ``1 image (640×480 PNG, 118 KB)`` — exactly as the terminal client paints it.
    The alternative, which this used to do, was ``str()`` over the content, and that
    puts a `\\x89PNG\\r\\n` repr in the middle of the conversation: unreadable for a
    person and a megabyte of escaped bytes in the response.
    """
    items: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        for part in getattr(message, 'parts', ()):
            item = _part_view(part, index)
            if item is not None:
                items.append(item)
    return items


def attachment_view(part: BinaryContent) -> dict[str, Any]:
    """One binary attachment, as data.

    It carries the size and the pixels because that is the only thing a reader can
    use; the payload itself stays on the server. ``label`` is precomputed so a client
    without an image decoder still says something true.
    """
    width, height = image_size(part.data)
    kind = 'image' if part.is_image else 'binary'
    suffix = str(part.media_type).rsplit('/', 1)[-1].upper()
    sized = f'{width}×{height} {suffix}' if width and height else f'{suffix} {human_bytes(len(part.data))}'
    return {
        'kind': kind,
        'media_type': str(part.media_type),
        'width': width,
        'height': height,
        'bytes': len(part.data),
        'size': human_bytes(len(part.data)),
        'label': sized,
        'identifier': part.identifier,
    }


def _content_view(content: Any) -> tuple[str, list[dict[str, Any]]]:
    """The text of a message and the attachments it carries, apart.

    This is the same split the terminal client makes: the text as it was typed — the
    `[Image #1]` markers included, because they are what ties a sentence to a picture
    — and the images as descriptions. A plain string is the common case and comes
    back untouched.
    """
    if isinstance(content, str):
        return content, []
    if isinstance(content, BinaryContent):
        return '', [attachment_view(content)]
    if isinstance(content, (list, tuple)):
        texts = [item for item in content if isinstance(item, str)]
        attachments = [attachment_view(item) for item in content if isinstance(item, BinaryContent)]
        return ' '.join(texts), attachments
    return _text(content), []


def _described(content: Any) -> str:
    """The text of a possibly-binary result: an image becomes its description.

    A tool that returns an image — `read_file` on a PNG, an image tool — puts a
    `BinaryContent` in the history. Stringifying it produces the repr, so it is
    described instead, in the same shape the user's own attachments use.
    """
    text, attachments = _content_view(content)
    if not attachments:
        return text
    described = ', '.join(
        f'{item["label"]}, {item["size"]}' if item['width'] else item['label'] for item in attachments
    )
    noun = 'image' if len(attachments) == 1 else 'images'
    prefix = f'{len(attachments)} {noun} ({described})'
    return f'{text}\n{prefix}' if text else prefix


def _part_view(part: Any, index: int) -> dict[str, Any] | None:
    kind = getattr(part, 'part_kind', '')
    if kind == 'user-prompt':
        text, attachments = _content_view(getattr(part, 'content', ''))
        item: dict[str, Any] = {'kind': 'user', 'index': index, 'text': _cut(text)}
        if attachments:
            item['attachments'] = attachments
        return item
    if kind == 'system-prompt':
        return None
    if kind == 'text':
        return {'kind': 'assistant', 'index': index, 'text': _cut(_text(getattr(part, 'content', '')))}
    if kind == 'thinking':
        return {'kind': 'thinking', 'index': index, 'text': _cut(_text(getattr(part, 'content', '')))}
    if kind == 'tool-call':
        return {
            'kind': 'tool_call',
            'index': index,
            'tool_name': _text(getattr(part, 'tool_name', '')),
            'call_id': _text(getattr(part, 'tool_call_id', '')),
            'args': _jsonable(getattr(part, 'args', None)),
        }
    if kind == 'tool-return':
        # `outcome` is `success` or `retried`: a retry is a tool that failed and is
        # being told why, so it paints as a failure. Reading only `part_kind` marked
        # every retry green.
        ok = getattr(part, 'outcome', 'success') == 'success'
        return {
            'kind': 'tool_result',
            'index': index,
            'tool_name': _text(getattr(part, 'tool_name', '')),
            'call_id': _text(getattr(part, 'tool_call_id', '')),
            'text': _cut(_described(getattr(part, 'content', ''))),
            'ok': ok,
        }
    if kind == 'retry-prompt':
        return {
            'kind': 'tool_result',
            'index': index,
            'tool_name': _text(getattr(part, 'tool_name', '')),
            'call_id': _text(getattr(part, 'tool_call_id', '')),
            'text': _cut(_described(getattr(part, 'content', ''))),
            'ok': False,
        }
    if kind in {'builtin-tool-call', 'builtin-tool-return'}:
        is_call = kind.endswith('call')
        return {
            'kind': 'tool_call' if is_call else 'tool_result',
            'index': index,
            'tool_name': _text(getattr(part, 'tool_name', '')),
            'call_id': _text(getattr(part, 'tool_call_id', '')),
            'text': '' if is_call else _cut(_described(getattr(part, 'content', ''))),
            'ok': True,
        }
    # A file or audio part: described, never inlined.
    if isinstance(part, BinaryContent):
        return {'kind': 'assistant', 'index': index, 'text': _described(part)}
    return None


def _jsonable(value: Any) -> Any:
    """Arguments as JSON, cut if they are huge. Bytes become a size, not a payload."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return f'<{len(value)} bytes>'
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return _text(value)


def model_view(snapshot: Any) -> dict[str, Any]:
    """The active model, its window and the effort levels it accepts."""
    account = getattr(snapshot, 'account', None)
    return {
        'configured': bool(getattr(snapshot, 'is_configured', False)),
        'account': None if account is None else {'id': account.id, 'alias': account.alias, 'kind': str(account.kind)},
        'model': _text(getattr(snapshot, 'model', '')),
        'thinking': _text(getattr(snapshot, 'thinking', 'default')),
        'label': _text(getattr(snapshot, 'label', '')),
        'window': getattr(snapshot, 'window', None),
        'efforts': [str(level) for level in getattr(snapshot, 'efforts', ())],
        'images': bool(getattr(snapshot, 'images', True)),
        'notice': getattr(snapshot, 'notice', None),
    }


def agent_view(profile: Any, *, active: bool) -> dict[str, Any]:
    """One agent profile, as the picker paints it."""
    return {
        'name': getattr(profile, 'name', ''),
        'description': getattr(profile, 'description', ''),
        'role': str(getattr(profile, 'role', '')),
        'source': getattr(profile, 'source', ''),
        'max_steps': getattr(profile, 'max_steps', None),
        'active': active,
    }


__all__ = [
    'MAX_ITEM_CHARS',
    'agent_view',
    'mcp_view',
    'model_view',
    'node_view',
    'roadmap_view',
    'session_view',
    'skills_view',
    'summary_view',
    'transcript',
    'tree_view',
    'workspace_view',
]
