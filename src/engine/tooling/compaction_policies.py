"""What each Sigil tool keeps of its arguments and of its result when compacted.

Every tool this project owns declares a policy here and passes it to its registration.
The declaration is mandatory: the agent build fails when one is missing, because a tool
without a policy is a cost nobody decided, and the moment to notice is not the bill.

Rules the functions keep:

- **Identity survives.** A path, a URL, a server name, an exit code, a status and an
  error are what let the model decide whether it needs the call again. Only the payload
  shrinks.
- **A failure never reads like a success.** The error text is shortened, never dropped,
  and the same goes for a partial result and its reason.
- **Both sides matter.** The argument carries the path or the query; the result carries
  the body, which is why each tool declares two functions.
- **Nothing is invented.** A compacted value is a projection of the original, never a
  summary written by a model and never a guess about what it said.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.engine.tooling.compaction import Compactor, ToolCompactor, contains_binary, shorten
from src.engine.tooling.compaction_constants import SEMANTIC_COMPACT_CHARS


def _limit(name: str) -> int:
    return SEMANTIC_COMPACT_CHARS[name]


# --- helpers --------------------------------------------------------------


def _as_mapping(value: Any) -> dict[str, Any]:
    """The value as a mapping, whichever shape it arrived in.

    A live call carries the pydantic model the tool returned; a resumed one carries
    what the session stored. Both have to compact the same way.
    """
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, 'model_dump', None)
    if callable(dump):
        data = dump()
        if isinstance(data, Mapping):
            return dict(data)
    return {'value': value}


def _items(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _compact_fields(value: Any, keep: Sequence[str], limit: int, *, text_keys: Sequence[str] = ()) -> Any:
    """Keeps ``keep`` as they are and shortens the strings named in ``text_keys``.

    A result stored as plain text has no fields to keep: it is shortened as text instead
    of being emptied, which is what a resumed transcript carries for most tools.
    """
    if isinstance(value, str):
        return shorten(value, limit, label='tool result')
    data = _as_mapping(value)
    result: dict[str, Any] = {}
    for name in (
        *keep,
        'status',
        'outcome',
        'error',
        'persistence_error',
        'binary',
        'encoding',
        'mime_type',
        'omitted_fields',
    ):
        if name in data:
            result[name] = data[name]
    for name in text_keys:
        if name in data and data[name] is not None:
            result[name] = shorten(str(data[name]), limit)
    removed = sorted((set(data) - set(result)) | set(data.get('omitted_fields', [])))
    if removed:
        result['omitted_fields'] = removed
    return result


def _binary_stub(value: Any, keep: Sequence[str], label: str) -> Any:
    """An opaque payload keeps only its handles: it cannot be partially decoded."""
    if isinstance(value, str):
        return shorten(value, _limit('resource_note'), label='tool result')
    data = _as_mapping(value)
    result = {name: data[name] for name in (*keep, 'error', 'status', 'outcome') if name in data}
    result.setdefault('omitted', label)
    return result


def _policy(input_fn: Compactor, output_fn: Compactor, *, preserve: Sequence[str] = ()) -> ToolCompactor:
    return ToolCompactor(input=input_fn, output=output_fn, preserve=frozenset(preserve))


# --- shell ----------------------------------------------------------------


def _run_input(args: dict[str, Any]) -> dict[str, Any]:
    """Commands are what the call was: they shrink, they never disappear."""
    limit = _limit('process_input')
    cmds = [shorten(str(command), limit) for command in _items(args.get('cmds', []))]
    return {'cmds': cmds, 'timeout_seconds': args.get('timeout_seconds')}


def _run_output(value: Any) -> Any:
    """Keeps exit code, timeout and error; the streams are what shrink."""
    limit = _limit('process_output')
    results = []
    for item in _items(value):
        compacted = _compact_fields(
            item,
            ('command', 'returncode', 'timed_out', 'stdout_truncated', 'stderr_truncated'),
            limit,
            text_keys=('stdout', 'stderr', 'error'),
        )
        if isinstance(compacted, dict):
            data = _as_mapping(item)
            for stream in ('stdout', 'stderr'):
                if stream in compacted and compacted[stream] != data.get(stream):
                    compacted[f'{stream}_truncated'] = True
        results.append(compacted)
    return results


RUN_COMPACTOR = _policy(_run_input, _run_output, preserve=('timeout_seconds',))


# --- files ----------------------------------------------------------------


def _path_input(args: dict[str, Any]) -> dict[str, Any]:
    """The path is the identity of the call: it always survives."""
    kept = {'path': args.get('path')}
    for name in ('start_line', 'end_line', 'max_bytes', 'append', 'replace_all'):
        if args.get(name) is not None:
            kept[name] = args[name]
    for name in ('content', 'new_string', 'old_string', 'expected_text'):
        if args.get(name) is not None:
            kept[name] = shorten(str(args[name]), _limit('file_edit_input'))
    return kept


def _file_output(value: Any) -> Any:
    """A read keeps its path and range; the text is the payload."""
    limit = _limit('file_output')
    keep = (
        'path',
        'size',
        'truncated',
        'binary',
        'encoding',
        'created',
        'appended',
        'bytes_written',
        'mode',
        'start_line',
        'end_line',
        'total_lines',
        'replacements',
        'bytes_before',
        'bytes_after',
    )
    if isinstance(value, Mapping) and contains_binary(value):
        return _binary_stub(value, keep, 'binary file content')
    return _compact_fields(value, keep, limit, text_keys=('content', 'text', 'preview'))


READ_FILE_COMPACTOR = _policy(_path_input, _file_output, preserve=('path',))
WRITE_FILE_COMPACTOR = _policy(_path_input, _file_output, preserve=('path',))
PATCH_FILE_COMPACTOR = _policy(_path_input, _file_output, preserve=('path',))


# --- web ------------------------------------------------------------------


def _search_input(args: dict[str, Any]) -> dict[str, Any]:
    limit = _limit('search_input')
    kept = {'query': shorten(str(args.get('query', '')), limit)}
    for name in ('max_results', 'region', 'safesearch', 'timelimit'):
        if args.get(name) is not None:
            kept[name] = args[name]
    return kept


def _search_output(value: Any) -> Any:
    """Title and URL before snippet: they are what makes a result worth reading."""
    data = _as_mapping(value)
    limit = _limit('search_snippet')
    results = []
    for item in _items(data.get('results', [])):
        entry = _as_mapping(item)
        compacted = {name: entry[name] for name in ('title', 'url') if name in entry}
        if 'snippet' in entry:
            compacted['snippet'] = shorten(str(entry['snippet']), limit)
        results.append(compacted)
    return {'query': data.get('query'), 'count': data.get('count'), 'results': results}


def _url_input(args: dict[str, Any]) -> dict[str, Any]:
    return {
        'url': args.get('url', ''),
        'fmt': args.get('fmt'),
        'max_chars': args.get('max_chars'),
    }


def _page_output(value: Any) -> Any:
    limit = _limit('page_output')
    if contains_binary(value):
        return _binary_stub(value, ('url', 'format', 'truncated'), 'binary page content')
    return _compact_fields(value, ('url', 'format', 'truncated'), limit, text_keys=('content',))


WEB_SEARCH_COMPACTOR = _policy(_search_input, _search_output)
READ_URL_COMPACTOR = _policy(_url_input, _page_output)


# --- skills ---------------------------------------------------------------


def _name_input(args: dict[str, Any]) -> dict[str, Any]:
    """The name and the path name the artifact: both survive."""
    kept = {name: args[name] for name in ('name', 'path', 'script', 'timeout_seconds') if args.get(name) is not None}
    if args.get('args'):
        limit = _limit('script_argument')
        kept['args'] = [shorten(str(item), limit) for item in _items(args['args'])]
    if args.get('query'):
        kept['query'] = shorten(str(args['query']), _limit('search_input'))
    if args.get('max_bytes') is not None:
        kept['max_bytes'] = args['max_bytes']
    return kept


def _skill_list_output(value: Any) -> Any:
    limit = _limit('skill_description')
    return [
        _compact_fields(item, ('name', 'source', 'path'), limit, text_keys=('description',)) for item in _items(value)
    ]


def _loaded_skill_output(value: Any) -> Any:
    """The instructions are injected elsewhere; here only the inventory matters."""
    data = _as_mapping(value)
    limit = _limit('loaded_skill_description')
    resources = [_compact_fields(item, ('path', 'size', 'kind'), limit) for item in _items(data.get('resources', []))]
    kept = _compact_fields(data, ('name', 'source', 'path'), limit, text_keys=('description',))
    kept['resources'] = resources
    return kept


def _script_output(value: Any) -> Any:
    result = _run_output(value)[0]
    if isinstance(result, dict):
        data = _as_mapping(value)
        for key in ('skill', 'script'):
            if key in data:
                result[key] = data[key]
    return result


def _resource_output(value: Any) -> Any:
    return _compact_fields(value, ('skill', 'path', 'size', 'truncated', 'binary', 'note'), _limit('resource_note'))


LIST_SKILLS_COMPACTOR = _policy(_name_input, _skill_list_output)
LOAD_SKILL_COMPACTOR = _policy(_name_input, _loaded_skill_output)
READ_SKILL_RESOURCE_COMPACTOR = _policy(_name_input, _resource_output)
RUN_SKILL_SCRIPT_COMPACTOR = _policy(_name_input, _script_output)


# --- interaction and roadmap ---------------------------------------------


def _questions_input(args: dict[str, Any]) -> dict[str, Any]:
    limit = _limit('question_input')
    questions = []
    for item in _items(args.get('questions', [])):
        entry = _as_mapping(item)
        compacted = {}
        if 'question' in entry:
            compacted['question'] = shorten(str(entry['question']), limit)
        compacted['options'] = [
            shorten(str(_as_mapping(option).get('label', '')), _limit('question_option'))
            for option in _items(entry.get('options', []))
        ]
        questions.append(compacted)
    return {'questions': questions}


def _question_result_output(value: Any) -> Any:
    """An answer is the user's decision: it shrinks last and never disappears."""
    # User decisions are protected verbatim: even a truncated negation changes authority.
    return _compact_fields(
        value, ('answers', 'cancelled'), _limit('question_result_note'), text_keys=('note', 'reason')
    )


ASK_USER_COMPACTOR = _policy(_questions_input, _question_result_output)


def _roadmap_input(args: dict[str, Any]) -> dict[str, Any]:
    kept = {name: args[name] for name in ('action', 'item_id', 'status') if args.get(name) is not None}
    limit = _limit('roadmap_input')
    if args.get('title'):
        kept['title'] = shorten(str(args['title']), limit)
    if args.get('notes'):
        kept['notes'] = shorten(str(args['notes']), limit)
    if args.get('items'):
        kept['items'] = [
            _compact_fields(item, ('id', 'status'), limit, text_keys=('title',)) for item in _items(args['items'])
        ]
    return kept


def _roadmap_output(value: Any) -> Any:
    limit = _limit('roadmap_output')
    data = _as_mapping(value)
    items = [
        _compact_fields(item, ('id', 'status'), limit, text_keys=('title',)) for item in _items(data.get('items', []))
    ]
    return {**_compact_fields(data, ('counts', 'persistence_error'), limit), 'items': items}


UPDATE_ROADMAP_COMPACTOR = _policy(_roadmap_input, _roadmap_output)


# --- images ---------------------------------------------------------------


def _image_input(args: dict[str, Any]) -> dict[str, Any]:
    return {'source': args.get('source', '')}


def _image_output(value: Any) -> Any:
    """The picture itself is never part of this text: the id is the handle."""
    if isinstance(value, str):
        # The return value is already a short sentence with the recoverable image id.
        return value
    return _compact_fields(value, ('image_id', 'width', 'height'), _limit('image_note'), text_keys=('note', 'error'))


VIEW_IMAGE_COMPACTOR = _policy(_image_input, _image_output)


# --- agent tree -----------------------------------------------------------


def _tree_input(args: dict[str, Any]) -> dict[str, Any]:
    kept = {name: args[name] for name in ('agent', 'agent_id', 'mode') if args.get(name) is not None}
    limit = _limit('agent_input')
    if args.get('task'):
        kept['task'] = shorten(str(args['task']), limit)
    if args.get('message'):
        kept['message'] = shorten(str(args['message']), limit)
    if args.get('notification_ids'):
        kept['notification_ids'] = list(args['notification_ids'])
    return kept


def _text_output(value: Any) -> Any:
    limit = _limit('agent_output')
    if isinstance(value, str):
        header, separator, body = value.partition('\n')
        return header + (separator + shorten(body, limit) if separator else '')
    return _compact_fields(value, ('agent_id', 'name', 'status', 'delivered'), limit, text_keys=('result', 'error'))


def _agents_list_output(value: Any) -> Any:
    limit = _limit('agent_task')
    if isinstance(value, str):
        # Preserve every row's identifiers and state; only the free-form task shrinks.
        rows = []
        for row in value.splitlines():
            fields = row.split(' | ')
            rows.append(' | '.join([*fields[:-1], shorten(fields[-1], limit)]) if len(fields) > 1 else row)
        return '\n'.join(rows)
    return [_compact_fields(item, ('id', 'name', 'status', 'role'), limit) for item in _items(value)]


SUMMON_COMPACTOR = _policy(_tree_input, _text_output)
SEND_MESSAGE_COMPACTOR = _policy(_tree_input, _text_output)
ACK_NOTIFICATIONS_COMPACTOR = _policy(_tree_input, _text_output)
KILL_SUBAGENT_COMPACTOR = _policy(_tree_input, _text_output)
LIST_AGENTS_COMPACTOR = _policy(_tree_input, _agents_list_output)


# --- mcp ------------------------------------------------------------------


def _mcp_output(value: Any) -> Any:
    limit = _limit('mcp_output')
    if isinstance(value, str):
        return shorten(value, limit)
    data = _as_mapping(value)
    keep = ('server', 'uri', 'name', 'status', 'error', 'binary', 'encoding', 'mime_type')
    if contains_binary(data):
        return _binary_stub(data, keep, 'binary resource content')
    result = _compact_fields(data, keep, limit, text_keys=('content', 'description'))
    if 'messages' in data:
        result['messages'] = [
            _compact_fields(item, ('role',), limit, text_keys=('content',)) for item in _items(data['messages'])
        ]
    return result


def _mcp_list_output(value: Any) -> Any:
    limit = _limit('mcp_description')
    return [
        _compact_fields(
            item,
            (
                'name',
                'server',
                'uri',
                'status',
                'enabled',
                'transport',
                'prefix',
                'source',
                'tools',
                'mime_type',
                'arguments',
            ),
            limit,
            text_keys=('description', 'error'),
        )
        for item in _items(value)
    ]


def _mcp_prompt_input(args: dict[str, Any]) -> dict[str, Any]:
    kept = {name: args[name] for name in ('server', 'name', 'connect') if args.get(name) is not None}
    if args.get('uri'):
        kept['uri'] = args['uri']
    if args.get('arguments'):
        kept['arguments'] = {
            str(key): shorten(str(item), _limit('mcp_argument')) for key, item in _as_mapping(args['arguments']).items()
        }
    return kept


LIST_MCP_SERVERS_COMPACTOR = _policy(_mcp_prompt_input, _mcp_list_output)
LIST_MCP_RESOURCES_COMPACTOR = _policy(_mcp_prompt_input, _mcp_list_output)
READ_MCP_RESOURCE_COMPACTOR = _policy(_mcp_prompt_input, _mcp_output)
LIST_MCP_PROMPTS_COMPACTOR = _policy(_mcp_prompt_input, _mcp_list_output)
GET_MCP_PROMPT_COMPACTOR = _policy(_mcp_prompt_input, _mcp_output)


ALL_COMPACTORS: dict[str, ToolCompactor] = {
    'run': RUN_COMPACTOR,
    'read_file': READ_FILE_COMPACTOR,
    'write_file': WRITE_FILE_COMPACTOR,
    'patch_file': PATCH_FILE_COMPACTOR,
    'web_search': WEB_SEARCH_COMPACTOR,
    'read_url': READ_URL_COMPACTOR,
    'list_skills': LIST_SKILLS_COMPACTOR,
    'load_skill': LOAD_SKILL_COMPACTOR,
    'read_skill_resource': READ_SKILL_RESOURCE_COMPACTOR,
    'run_skill_script': RUN_SKILL_SCRIPT_COMPACTOR,
    'ask_user': ASK_USER_COMPACTOR,
    'update_roadmap': UPDATE_ROADMAP_COMPACTOR,
    'view_image': VIEW_IMAGE_COMPACTOR,
    'summon': SUMMON_COMPACTOR,
    'send_message': SEND_MESSAGE_COMPACTOR,
    'ack_notifications': ACK_NOTIFICATIONS_COMPACTOR,
    'kill_subagent': KILL_SUBAGENT_COMPACTOR,
    'list_agents': LIST_AGENTS_COMPACTOR,
    'list_mcp_servers': LIST_MCP_SERVERS_COMPACTOR,
    'list_mcp_resources': LIST_MCP_RESOURCES_COMPACTOR,
    'read_mcp_resource': READ_MCP_RESOURCE_COMPACTOR,
    'list_mcp_prompts': LIST_MCP_PROMPTS_COMPACTOR,
    'get_mcp_prompt': GET_MCP_PROMPT_COMPACTOR,
}
"""Every tool this project owns, by name. The build checks the catalogue against it."""


__all__ = ['ALL_COMPACTORS']
