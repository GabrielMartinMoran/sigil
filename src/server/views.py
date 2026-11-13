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

import json
from collections.abc import Sequence
from typing import Any

from pydantic_ai.messages import BinaryContent, ModelMessage, ModelMessagesTypeAdapter

from src.engine.agents.model_config import AgentModelConfig
from src.engine.agents.model_defaults import ConfigFileState, ModelDefaultsState
from src.engine.agents.rollback import PROMPT_KIND, Checkpoint, ForkOutcome, RollbackOutcome, prompt_of_turn
from src.engine.agents.tree.models.agent_node import AgentNode
from src.engine.agents.tree.tree import AgentTree
from src.engine.context.images import image_size
from src.engine.context.meter.cost import SessionCost
from src.engine.context.meter.snapshot import ContextSnapshot
from src.engine.mcp.tool_models.server_status import ServerStatus
from src.engine.providers.domain.model_capability import ModelCatalog
from src.engine.roadmap.models.todo_item import TodoItem
from src.engine.sessions.session_store import Session, SessionSummary
from src.engine.sessions.titling import shorten
from src.engine.skills.loaded import loaded_skills
from src.engine.storage.accepted_inputs import _record
from src.engine.storage.database import Database
from src.engine.storage.executions import root_id_for
from src.engine.tooling.outputs import present_output
from src.engine.tooling.presentation import human_bytes
from src.engine.workspace.models.worktree import Workspace

MAX_ITEM_CHARS = 200_000
"""Ceiling of one projected text block.

A single tool result is already capped by the engine; this is the backstop for a
transcript read that would otherwise put a hundred megabytes in one JSON body.
"""


class SessionNotStored(LookupError):
    """The session exists only in memory, so there is no durable row to project.

    ``Composition`` opens a *draft* session — an id and a workspace with nothing
    saved under it yet — until the first message gives it a title and a row. Asking
    for its transcript is a legitimate question with a legitimate answer, "nothing
    yet", and it is not a server failure: the route answers a typed 404 that a client
    reads as an empty conversation. It stays a `LookupError` so every caller that
    already treats a missing row that way keeps working.
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
    """The open conversation: what it is called and what it runs on.

    ``external_files`` and ``mcp_overrides`` are the two settings of the session
    that a client can toggle. They travel here because the row is where they
    live: a command that changed one has to be readable from the same projection
    the screen is painted from, not only from its own reply.
    """
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
        'external_files': session.allow_external_files,
        'mcp_overrides': dict(session.mcp_overrides),
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


def durable_session(database: Database, session_id: str) -> dict[str, Any]:
    """One coherent read of the durable session: transcript, queue and recovery facts.

    The transcript is rebuilt from the messages rows, not from the live session in
    memory. ``pending`` holds accepted or claimed inputs without an
    ``input_deliveries`` fact; delivered rows are read from the transcript and never
    reappear as pending. Recovery copies are listed with availability, and entries
    whose delivery went to a recovery copy are kept in a separate section. Nothing
    is merged across transcripts.
    """
    with database.transaction() as conn:
        row = conn.execute('SELECT id, title, updated_at, preview FROM sessions WHERE id = ?', (session_id,)).fetchone()
        if row is None:
            raise SessionNotStored(f'No session {session_id}.')
        messages = [
            ModelMessagesTypeAdapter.validate_python([json.loads(str(message['payload']))])[0]
            for message in conn.execute(
                'SELECT payload FROM messages WHERE execution_id = ? ORDER BY seq',
                (root_id_for(session_id),),
            )
        ]
        queue_rows = conn.execute(
            'SELECT * FROM accepted_inputs WHERE session_id = ? ORDER BY sequence', (session_id,)
        ).fetchall()
        facts = {
            str(fact['request_id']): fact
            for fact in conn.execute(
                'SELECT request_id, checkpointed, destination_id, destination_session_id '
                'FROM input_deliveries WHERE session_id = ?',
                (session_id,),
            )
        }
        links = conn.execute(
            'SELECT destination_id, destination_session_id FROM recovery_links '
            'WHERE original_session_id = ? ORDER BY destination_id',
            (session_id,),
        ).fetchall()

        def available(destination_id: Any, destination_session_id: Any) -> bool:
            if destination_id is None or destination_session_id is None:
                return False
            return conn.execute('SELECT 1 FROM sessions WHERE id = ?', (destination_session_id,)).fetchone() is not None

        entries: list[dict[str, Any]] = []
        for queue_row in queue_rows:
            record = _record(queue_row)
            fact = facts.get(record.request_id)
            destination = None if fact is None else str(fact['destination_id'])
            destination_session = None if fact is None else fact['destination_session_id']
            entries.append(
                {
                    'request_id': record.request_id,
                    'sequence': record.sequence,
                    'kind': record.kind,
                    'state': record.state,
                    'text': record.text,
                    'image_count': len(record.images),
                    'delivered': fact is not None,
                    'checkpointed': None if fact is None else bool(fact['checkpointed']),
                    'destination_id': destination,
                    'destination_available': available(destination, destination_session),
                }
            )

        recoveries = [
            {
                'recovery_session_id': str(link['destination_id']),
                'available': available(link['destination_id'], link['destination_session_id']),
            }
            for link in links
        ]
        pending = [entry for entry in entries if entry['state'] in ('accepted', 'claimed') and not entry['delivered']]
        conflicted = [
            entry for entry in entries if entry['delivered'] and entry['destination_id'] not in (None, session_id)
        ]
        return {
            'session': {
                'id': str(row['id']),
                'title': row['title'],
                'updated_at': str(row['updated_at']),
                'preview': row['preview'],
            },
            'transcript': transcript(messages),
            'skills': skills_view(messages),
            'queue': entries,
            'pending': pending,
            'deliveries': [
                {
                    'request_id': request_id,
                    'checkpointed': bool(fact['checkpointed']),
                    'destination_id': fact['destination_id'],
                    'destination_available': available(fact['destination_id'], fact['destination_session_id']),
                }
                for request_id, fact in facts.items()
            ],
            'recoveries': recoveries,
            'conflicted_entries': conflicted,
        }


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


def _has_binary(value: Any) -> bool:
    return (
        isinstance(value, (BinaryContent, bytes))
        or isinstance(value, (list, tuple))
        and any(isinstance(item, BinaryContent) for item in value)
    )


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
        shown = present_output(part.tool_name, part.content)
        return {
            'kind': 'tool_result',
            'index': index,
            'tool_name': _text(getattr(part, 'tool_name', '')),
            'call_id': _text(getattr(part, 'tool_call_id', '')),
            'text': _cut(shown.text) if not _has_binary(part.content) else _cut(_described(part.content)),
            'summary': None if shown.summary is None else {'text': shown.summary.text, 'status': shown.summary.status},
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
    """The active model, its window and the effort levels it accepts.

    ``images`` is ``None`` while no model is configured: `ModelSnapshot` defaults it
    to true, which is a harmless default inside the process that owns the model but
    a false fact once it crosses the boundary. A client that cannot know has to be
    able to say "unknown" instead of accepting an image the provider will refuse.
    """
    configured = bool(getattr(snapshot, 'is_configured', False))
    account = getattr(snapshot, 'account', None)
    return {
        'configured': configured,
        'account': None if account is None else {'id': account.id, 'alias': account.alias, 'kind': str(account.kind)},
        'model': _text(getattr(snapshot, 'model', '')),
        'thinking': _text(getattr(snapshot, 'thinking', 'default')),
        'label': _text(getattr(snapshot, 'label', '')),
        'window': getattr(snapshot, 'window', None),
        'efforts': [str(level) for level in getattr(snapshot, 'efforts', ())],
        'images': bool(getattr(snapshot, 'images', False)) if configured else None,
        'notice': getattr(snapshot, 'notice', None),
    }


def model_defaults_view(state: ModelDefaultsState, *, accounts: Sequence[Any]) -> dict[str, Any]:
    """Both config files that decide what a profile starts on, per file.

    Per file and never merged: which file an entry belongs to is a decision the user
    takes when they write it, and the screen says so. The precedence between the two
    is not decided here either — ``targets`` carries `ModelDefaultsState`'s own
    answers, so no client has to hold a second copy of that rule.

    ``accounts`` is what the service has configured. An entry that names an account
    that is gone then reads as that, instead of as an alias nobody can place.

    ``exists`` is the one fact read from the filesystem rather than from the state: a
    file that is not there yet is a real state for the screen (the first write creates
    it), and the reader treats it exactly like an empty one.
    """
    known = {str(getattr(account, 'alias', '')) for account in accounts}
    return {
        'fixed': None if state.fixed is None else str(state.fixed),
        'destinations': [str(scope) for scope in state.destinations],
        'files': [_defaults_file_view(scope, state.files[scope]) for scope in state.destinations],
        'targets': [
            {
                'target': target,
                'effective': _defaults_entry_view(state.effective(target)),
                'invalid': state.invalid(target),
                'sources': [str(scope) for scope in state.destinations if scope in state.sources(target)],
            }
            for target in (None, *state.agent_names())
        ],
        'accounts': [{'alias': alias, 'configured': alias in known} for alias in _named_accounts(state)],
        'errors': list(state.errors),
    }


def _defaults_file_view(scope: Any, file: ConfigFileState) -> dict[str, Any]:
    """One config file: where it is, what it says, and what it could not parse."""
    return {
        'scope': str(scope),
        'path': str(file.path),
        'exists': file.path.is_file(),
        'default': _defaults_entry_view(file.default),
        'default_invalid': file.default_invalid,
        'agents': {name: _defaults_entry_view(config) for name, config in file.agents.items()},
        'invalid_agents': sorted(file.invalid_agents),
        'errors': list(file.errors),
    }


def _defaults_entry_view(config: AgentModelConfig | None) -> dict[str, Any] | None:
    """One entry, with the three keys the file itself holds."""
    if config is None:
        return None
    return {'account': config.account, 'model': config.model, 'effort': config.effort}


def _named_accounts(state: ModelDefaultsState) -> list[str]:
    """Every account the entries name, so a client can say which one is not configured."""
    aliases: set[str] = set()
    for scope in state.destinations:
        file = state.files[scope]
        entries = (file.default, *file.agents.values())
        aliases.update(config.account for config in entries if config is not None)
    return sorted(aliases)


def account_view(account: Any, *, active: bool, has_credential: bool) -> dict[str, Any]:
    """One provider account, as data.

    The credential is never here. `has_credential` is a boolean computed from the
    secret store in the process that owns it, and the key itself never leaves that
    process — not in this projection, not in an error, not in a log line.
    """
    return {
        'id': account.id,
        'alias': account.alias,
        'provider': str(getattr(account.kind, 'value', account.kind)),
        'provider_label': account.provider_label,
        'variant': str(getattr(account.variant, 'value', account.variant)),
        'auth': str(getattr(account.auth, 'value', account.auth)),
        'last_model': account.last_model,
        'has_credential': has_credential,
        'active': active,
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


def rollback_points(database: Database, checkpoints: list[Checkpoint]) -> list[dict[str, Any]]:
    """The turns a session can return to, newest first, with what opened each one.

    ``preview`` is the first line of the prompt that started the turn, not the
    turn's first message: a point is identified by what the user typed, and the
    first message of a turn can be a thought or a tool result. It is the same
    rule the terminal's picker applies, projected here so both clients list the
    same points in the same order.
    """
    return [
        rollback_point_view(
            checkpoint,
            preview=_turn_preview(database, checkpoint.turn_id),
            seq=_prompt_seq(database, checkpoint.turn_id),
        )
        for checkpoint in checkpoints
    ]


def rollback_point_view(checkpoint: Checkpoint, *, preview: str, seq: int | None = None) -> dict[str, Any]:
    """One point of the rollback list.

    The file cost of a restore is deliberately absent: it is a diff of the
    workspace, and a projection that cannot compute it leaves the field out
    instead of estimating one.
    """
    return {
        'turn_id': checkpoint.turn_id,
        'at': checkpoint.at,
        'reason': checkpoint.reason,
        'label': checkpoint.label,
        'preview': preview,
        'tree_commit': checkpoint.tree_commit,
        'seq': seq,
    }


def catalog_view(catalog: ModelCatalog) -> dict[str, Any]:
    """The models of one account, as data: the client builds its own rows.

    ``source`` says whether the list came from the provider or from the engine's
    fallback, and ``error`` why the remote one could not be read. The client needs
    both: a fallback list presented as the provider's would be a quieter lie than
    an error message.
    """
    return {
        'source': catalog.source,
        'error': catalog.error,
        'models': [
            {
                'name': model.name,
                'display_name': model.display_name,
                'window': model.context_window,
                'efforts': [str(level) for level in model.efforts],
            }
            for model in catalog.models
        ],
    }


def rollback_view(
    outcome: RollbackOutcome, *, prompt: str | None = None, prompt_images: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """What a rollback did, as data: the counts are the engine's, not an estimate.

    ``prompt`` is the text the user typed to open the turn that was dropped, read
    from the rows *before* they were truncated and carried here so a client can put
    it back in its composer. It is the argument the in-process terminal used to look
    up itself; the field changes no row and no count.
    """
    return {
        'target_turn_id': outcome.target_turn_id,
        'turns_dropped': outcome.turns_dropped,
        'executions_dropped': outcome.executions_dropped,
        'messages_dropped': outcome.messages_dropped,
        'files_restored': outcome.files_restored,
        'files_deleted': outcome.files_deleted,
        'files_unavailable': outcome.files_unavailable,
        'undo_checkpoint': outcome.undo_checkpoint,
        'prompt': prompt,
        'prompt_images': list(prompt_images or []),
    }


def fork_view(outcome: ForkOutcome) -> dict[str, Any]:
    """The session a fork created and how much of the conversation it kept."""
    return {
        'session_id': outcome.session_id,
        'turns_copied': outcome.turns_copied,
        'messages_copied': outcome.messages_copied,
        'executions_copied': outcome.executions_copied,
    }


def context_view(snapshot: ContextSnapshot, cost: SessionCost) -> dict[str, Any]:
    """How full the window is and what the session has cost, from the meter.

    ``used`` and ``percent`` are the **projected** numbers, the same pair the
    terminal's sidebar paints: the question is whether the next request fits, and
    the measured value describes the request that already happened. With a window
    and nothing measured the real usage is zero — a cold session paints 0%, not
    `?`, which is the rule the terminal applies.

    ``budget`` travels with the usage because the window is not all available:
    the tool schemas and the instruction blocks are sent on every request. They
    are the meter's measurements, and a session that has not made a request in
    this process has none yet; the numbers say that rather than guessing.
    """
    used = snapshot.projected
    percent = snapshot.projected_percent
    if used is None and snapshot.window:
        used = 0
        percent = 0.0
    budget = snapshot.budget
    return {
        'window': snapshot.window,
        'used': used,
        'percent': percent,
        'measured': snapshot.measured,
        'projected': snapshot.projected,
        'budget': {
            'tools': budget.tools,
            'instructions': budget.instructions,
            'output_reserve': budget.output_reserve,
            'safety_reserve': budget.safety_reserve,
            'available_for_history': budget.available_for_history,
        },
        'cost': {
            'total': None if cost.total is None else str(cost.total),
            'approximate': cost.approximate,
            'requests': cost.requests,
        },
    }


def _prompt_seq(database: Database, turn_id: str | None) -> int | None:
    """Where the turn's prompt sits in the transcript.

    A click on a message carries a `seq`; a rollback point carries a turn id. This
    column is what lets a client preselect the point the user actually clicked,
    without keeping a transcript of its own.
    """
    if turn_id is None:
        return None
    row = database.one(
        'SELECT seq FROM messages WHERE turn_id = ? AND payload LIKE ? ORDER BY seq LIMIT 1',
        (turn_id, f'%"part_kind":"{PROMPT_KIND}"%'),
    )
    return None if row is None else int(row['seq'])


def _turn_preview(database: Database, turn_id: str | None) -> str:
    """The prompt that opened a turn, on one line, or ``''`` when it is gone."""
    if turn_id is None:
        return ''
    prompt = prompt_of_turn(database, turn_id)
    if prompt is None:
        return ''
    text, images = prompt
    line = shorten(' '.join(text.split()))
    if not images:
        return line
    plural = 'image' if len(images) == 1 else 'images'
    return f'{line} +{len(images)} {plural}'.strip()


__all__ = [
    'MAX_ITEM_CHARS',
    'SessionNotStored',
    'agent_view',
    'context_view',
    'fork_view',
    'mcp_view',
    'model_defaults_view',
    'model_view',
    'node_view',
    'roadmap_view',
    'rollback_point_view',
    'rollback_points',
    'rollback_view',
    'session_view',
    'skills_view',
    'summary_view',
    'transcript',
    'tree_view',
    'workspace_view',
]
