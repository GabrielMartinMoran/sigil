"""What each Sigil tool keeps of its arguments and of its result, level by level.

Every tool this project owns declares a policy here and passes it to its registration.
The declaration is mandatory: the agent build fails when one is missing, because a tool
without a policy is a cost nobody decided, and the moment to notice is not the bill.

Rules the functions keep:

- **Identity survives every level.** A path, a URL, a server name, an exit code, a status
  and an error are what let the model decide whether it needs the call again. Only the
  payload shrinks.
- **A failure never reads like a success.** The error text is shortened, never dropped,
  and the same goes for a partial result and its reason.
- **Both sides matter.** The argument carries the path or the query; the result carries
  the body. A level applies to the two, which is why each tool declares two functions.
- **Nothing is invented.** A compacted value is a projection of the original, never a
  summary written by a model and never a guess about what it said.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.engine.tooling.lod import Compact, ToolLOD, ToolLODPolicy, contains_binary, limit_for, shorten
from src.engine.tooling.lod_constants import SCRIPT_ARGUMENT_COUNTS, SEMANTIC_LOD_CHARS


def _limit(name: str, level: ToolLOD) -> int:
    return SEMANTIC_LOD_CHARS[name][int(level) - 1]


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


def _stub(value: Any, keep: Sequence[str], label: str) -> Any:
    """The last level: what the call was, nothing of its payload."""
    if isinstance(value, str):
        return shorten(value, limit_for(ToolLOD.LOD_3), label='tool result')
    data = _as_mapping(value)
    result = {name: data[name] for name in (*keep, 'error', 'status', 'outcome', 'omitted_fields') if name in data}
    result.setdefault('omitted', label)
    return result


def _policy(
    input_fn: Compact,
    output_fn: Compact,
    *,
    preserve: Sequence[str] = (),
) -> ToolLODPolicy:
    return ToolLODPolicy(input=input_fn, output=output_fn, preserve=frozenset(preserve))


# --- shell ----------------------------------------------------------------


def _run_input(args: dict[str, Any], level: ToolLOD) -> dict[str, Any]:
    """Commands are what the call was: they shrink, they never disappear."""
    limit = _limit('process_input', level)
    cmds = [shorten(str(command), limit) for command in _items(args.get('cmds', []))]
    return {'cmds': cmds, 'timeout_seconds': args.get('timeout_seconds')}


def _run_output(value: Any, level: ToolLOD) -> Any:
    """Keeps exit code, timeout and error; the streams are what shrink."""
    limit = _limit('process_output', level)
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


RUN_LOD = _policy(_run_input, _run_output, preserve=('timeout_seconds',))


# --- files ----------------------------------------------------------------


def _path_input(args: dict[str, Any], level: ToolLOD) -> dict[str, Any]:
    """The path is the identity of the call: every level keeps it."""
    kept = {'path': args.get('path')}
    for name in ('start_line', 'end_line', 'max_bytes', 'append', 'replace_all'):
        if args.get(name) is not None:
            kept[name] = args[name]
    if level is ToolLOD.LOD_1:
        for name in ('content', 'new_string', 'old_string', 'expected_text'):
            if args.get(name) is not None:
                kept[name] = shorten(str(args[name]), _limit('file_edit_input', level))
    elif level is ToolLOD.LOD_2:
        for name in ('content', 'new_string'):
            if args.get(name) is not None:
                kept[name] = shorten(str(args[name]), _limit('file_edit_input', level))
    elif level is ToolLOD.LOD_3:
        kept = {name: kept[name] for name in ('path', 'start_line', 'end_line') if name in kept}
        kept.setdefault('omitted', 'edit payload')
    return kept


def _file_output(value: Any, level: ToolLOD) -> Any:
    """A read keeps its path and range; the text is the payload."""
    limit = _limit('file_output', level)
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
    data = _as_mapping(value) if not isinstance(value, str) else value
    if isinstance(data, dict) and contains_binary(data):
        return _stub(data, keep, 'binary file content')
    result = _compact_fields(value, keep, limit, text_keys=('content', 'text', 'preview'))
    if level is ToolLOD.LOD_3:
        result = _stub(result, keep, 'file content')
    return result


READ_FILE_LOD = _policy(_path_input, _file_output, preserve=('path',))
WRITE_FILE_LOD = _policy(_path_input, _file_output, preserve=('path',))
PATCH_FILE_LOD = _policy(_path_input, _file_output, preserve=('path',))


# --- web ------------------------------------------------------------------


def _search_input(args: dict[str, Any], level: ToolLOD) -> dict[str, Any]:
    limit = _limit('search_input', level)
    kept = {'query': shorten(str(args.get('query', '')), limit)}
    for name in ('max_results', 'region', 'safesearch', 'timelimit'):
        if args.get(name) is not None:
            kept[name] = args[name]
    return kept


def _search_output(value: Any, level: ToolLOD) -> Any:
    """Title and URL before snippet: they are what makes a result worth reading."""
    data = _as_mapping(value)
    limit = _limit('search_snippet', level)
    results = []
    for item in _items(data.get('results', [])):
        entry = _as_mapping(item)
        compacted = {name: entry[name] for name in ('title', 'url') if name in entry}
        if level is ToolLOD.LOD_1 and 'snippet' in entry:
            compacted['snippet'] = shorten(str(entry['snippet']), limit)
        results.append(compacted)
    return {'query': data.get('query'), 'count': data.get('count'), 'results': results}


def _url_input(args: dict[str, Any], level: ToolLOD) -> dict[str, Any]:
    return {
        'url': args.get('url', ''),
        'fmt': args.get('fmt'),
        'max_chars': args.get('max_chars'),
    }


def _page_output(value: Any, level: ToolLOD) -> Any:
    limit = _limit('page_output', level)
    if contains_binary(value):
        return _stub(value, ('url', 'format', 'truncated'), 'binary page content')
    result = _compact_fields(value, ('url', 'format', 'truncated'), limit, text_keys=('content',))
    if level is ToolLOD.LOD_3:
        result = _stub(result, ('url', 'format', 'truncated'), 'page text')
    return result


WEB_SEARCH_LOD = _policy(_search_input, _search_output)
READ_URL_LOD = _policy(_url_input, _page_output)


# --- skills ---------------------------------------------------------------


def _name_input(args: dict[str, Any], level: ToolLOD) -> dict[str, Any]:
    """The name and the path name the artifact: both survive every level."""
    kept = {name: args[name] for name in ('name', 'path', 'script', 'timeout_seconds') if args.get(name) is not None}
    if args.get('args'):
        count = SCRIPT_ARGUMENT_COUNTS[int(level) - 1]
        items = _items(args['args'])
        kept['args'] = [shorten(str(item), _limit('script_argument', level)) for item in items[:count]]
        if count is not None and len(items) > count:
            kept['omitted_arguments'] = len(items) - count
    if args.get('query'):
        kept['query'] = shorten(str(args['query']), _limit('search_input', level))
    if args.get('max_bytes') is not None:
        kept['max_bytes'] = args['max_bytes']
    return kept


def _skill_list_output(value: Any, level: ToolLOD) -> Any:
    limit = _limit('skill_description', level)
    return [
        _compact_fields(item, ('name', 'source', 'path'), limit, text_keys=('description',)) for item in _items(value)
    ]


def _loaded_skill_output(value: Any, level: ToolLOD) -> Any:
    """The instructions are injected elsewhere; here only the inventory matters."""
    data = _as_mapping(value)
    limit = _limit('loaded_skill_description', level)
    resources = [_compact_fields(item, ('path', 'size', 'kind'), limit) for item in _items(data.get('resources', []))]
    kept = _compact_fields(data, ('name', 'source', 'path'), limit, text_keys=('description',))
    kept['resources'] = resources
    if level is ToolLOD.LOD_3:
        kept['resources'] = [item.get('path') for item in resources]
    return kept


def _script_output(value: Any, level: ToolLOD) -> Any:
    result = _run_output(value, level)[0]
    if isinstance(result, dict):
        data = _as_mapping(value)
        for key in ('skill', 'script'):
            if key in data:
                result[key] = data[key]
    return result


def _resource_output(value: Any, level: ToolLOD) -> Any:
    return _compact_fields(
        value, ('skill', 'path', 'size', 'truncated', 'binary', 'note'), _limit('resource_note', level)
    )


LIST_SKILLS_LOD = _policy(_name_input, _skill_list_output)
LOAD_SKILL_LOD = _policy(_name_input, _loaded_skill_output)
READ_SKILL_RESOURCE_LOD = _policy(_name_input, _resource_output)
RUN_SKILL_SCRIPT_LOD = _policy(_name_input, _script_output)


# --- interaction and roadmap ---------------------------------------------


def _questions_input(args: dict[str, Any], level: ToolLOD) -> dict[str, Any]:
    limit = _limit('question_input', level)
    questions = []
    for item in _items(args.get('questions', [])):
        entry = _as_mapping(item)
        compacted = {}
        if 'question' in entry:
            compacted['question'] = shorten(str(entry['question']), limit)
        if level is ToolLOD.LOD_1:
            compacted['options'] = [
                shorten(str(_as_mapping(option).get('label', '')), _limit('question_option', level))
                for option in _items(entry.get('options', []))
            ]
        questions.append(compacted)
    return {'questions': questions}


def _question_result_output(value: Any, level: ToolLOD) -> Any:
    """An answer is the user's decision: it shrinks last and never disappears."""
    limit = _limit('question_result_note', level)
    # User decisions are protected verbatim: even a truncated negation changes authority.
    return _compact_fields(value, ('answers', 'cancelled'), limit, text_keys=('note', 'reason'))


ASK_USER_LOD = _policy(_questions_input, _question_result_output)


def _roadmap_input(args: dict[str, Any], level: ToolLOD) -> dict[str, Any]:
    kept = {name: args[name] for name in ('action', 'item_id', 'status') if args.get(name) is not None}
    limit = _limit('roadmap_input', level)
    if args.get('title'):
        kept['title'] = shorten(str(args['title']), limit)
    if args.get('notes'):
        kept['notes'] = shorten(str(args['notes']), limit)
    if args.get('items') and level is not ToolLOD.LOD_3:
        kept['items'] = [
            _compact_fields(item, ('id', 'status'), limit, text_keys=('title',)) for item in _items(args['items'])
        ]
    return kept


def _roadmap_output(value: Any, level: ToolLOD) -> Any:
    limit = _limit('roadmap_output', level)
    data = _as_mapping(value)
    items = [
        _compact_fields(item, ('id', 'status'), limit, text_keys=('title',)) for item in _items(data.get('items', []))
    ]
    return {**_compact_fields(data, ('counts', 'persistence_error'), limit), 'items': items}


UPDATE_ROADMAP_LOD = _policy(_roadmap_input, _roadmap_output)


# --- images ---------------------------------------------------------------


def _image_input(args: dict[str, Any], level: ToolLOD) -> dict[str, Any]:
    return {'source': args.get('source', '')}


def _image_output(value: Any, level: ToolLOD) -> Any:
    """The picture itself is never part of this text: the id is the handle."""
    if isinstance(value, str):
        # The return value is already a short sentence with the recoverable image id.
        return value
    limit = _limit('image_note', level)
    result = _compact_fields(value, ('image_id', 'width', 'height'), limit, text_keys=('note', 'error'))
    if level is ToolLOD.LOD_3:
        result = _stub(result, ('image_id',), 'image')
    return result


VIEW_IMAGE_LOD = _policy(_image_input, _image_output)


# --- agent tree -----------------------------------------------------------


def _tree_input(args: dict[str, Any], level: ToolLOD) -> dict[str, Any]:
    kept = {name: args[name] for name in ('agent', 'agent_id', 'mode') if args.get(name) is not None}
    limit = _limit('agent_input', level)
    if args.get('task'):
        kept['task'] = shorten(str(args['task']), limit)
    if args.get('message'):
        kept['message'] = shorten(str(args['message']), limit)
    if args.get('notification_ids'):
        kept['notification_ids'] = list(args['notification_ids'])
    return kept


def _text_output(value: Any, level: ToolLOD) -> Any:
    limit = _limit('agent_output', level)
    if isinstance(value, str):
        header, separator, body = value.partition('\n')
        return header + (separator + shorten(body, limit) if separator else '')
    return _compact_fields(value, ('agent_id', 'name', 'status', 'delivered'), limit, text_keys=('result', 'error'))


def _agents_list_output(value: Any, level: ToolLOD) -> Any:
    limit = _limit('agent_task', level)
    if isinstance(value, str):
        # Preserve every row's identifiers and state; only the free-form task shrinks.
        rows = []
        for row in value.splitlines():
            fields = row.split(' | ')
            rows.append(' | '.join([*fields[:-1], shorten(fields[-1], limit)]) if len(fields) > 1 else row)
        return '\n'.join(rows)
    return [_compact_fields(item, ('id', 'name', 'status', 'role'), limit) for item in _items(value)]


SUMMON_LOD = _policy(_tree_input, _text_output)
SEND_MESSAGE_LOD = _policy(_tree_input, _text_output)
ACK_NOTIFICATIONS_LOD = _policy(_tree_input, _text_output)
KILL_SUBAGENT_LOD = _policy(_tree_input, _text_output)
LIST_AGENTS_LOD = _policy(_tree_input, _agents_list_output)


# --- mcp ------------------------------------------------------------------


def _mcp_output(value: Any, level: ToolLOD) -> Any:
    limit = _limit('mcp_output', level)
    if isinstance(value, str):
        return shorten(value, limit)
    data = _as_mapping(value)
    keep = ('server', 'uri', 'name', 'status', 'error', 'binary', 'encoding', 'mime_type')
    if contains_binary(data):
        return _stub(data, keep, 'binary resource content')
    result = _compact_fields(data, keep, limit, text_keys=('content', 'description'))
    if 'messages' in data:
        result['messages'] = [
            _compact_fields(item, ('role',), limit, text_keys=('content',)) for item in _items(data['messages'])
        ]
    return result


def _mcp_list_output(value: Any, level: ToolLOD) -> Any:
    limit = _limit('mcp_description', level)
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


def _mcp_prompt_input(args: dict[str, Any], level: ToolLOD) -> dict[str, Any]:
    kept = {name: args[name] for name in ('server', 'name', 'connect') if args.get(name) is not None}
    if args.get('uri'):
        kept['uri'] = args['uri']
    if args.get('arguments') and level is not ToolLOD.LOD_3:
        kept['arguments'] = {
            str(key): shorten(str(item), _limit('mcp_argument', level))
            for key, item in _as_mapping(args['arguments']).items()
        }
    return kept


LIST_MCP_SERVERS_LOD = _policy(_mcp_prompt_input, _mcp_list_output)
LIST_MCP_RESOURCES_LOD = _policy(_mcp_prompt_input, _mcp_list_output)
READ_MCP_RESOURCE_LOD = _policy(_mcp_prompt_input, _mcp_output)
LIST_MCP_PROMPTS_LOD = _policy(_mcp_prompt_input, _mcp_list_output)
GET_MCP_PROMPT_LOD = _policy(_mcp_prompt_input, _mcp_output)


ALL_POLICIES: dict[str, ToolLODPolicy] = {
    'run': RUN_LOD,
    'read_file': READ_FILE_LOD,
    'write_file': WRITE_FILE_LOD,
    'patch_file': PATCH_FILE_LOD,
    'web_search': WEB_SEARCH_LOD,
    'read_url': READ_URL_LOD,
    'list_skills': LIST_SKILLS_LOD,
    'load_skill': LOAD_SKILL_LOD,
    'read_skill_resource': READ_SKILL_RESOURCE_LOD,
    'run_skill_script': RUN_SKILL_SCRIPT_LOD,
    'ask_user': ASK_USER_LOD,
    'update_roadmap': UPDATE_ROADMAP_LOD,
    'view_image': VIEW_IMAGE_LOD,
    'summon': SUMMON_LOD,
    'send_message': SEND_MESSAGE_LOD,
    'ack_notifications': ACK_NOTIFICATIONS_LOD,
    'kill_subagent': KILL_SUBAGENT_LOD,
    'list_agents': LIST_AGENTS_LOD,
    'list_mcp_servers': LIST_MCP_SERVERS_LOD,
    'list_mcp_resources': LIST_MCP_RESOURCES_LOD,
    'read_mcp_resource': READ_MCP_RESOURCE_LOD,
    'list_mcp_prompts': LIST_MCP_PROMPTS_LOD,
    'get_mcp_prompt': GET_MCP_PROMPT_LOD,
}
"""Every tool this project owns, by name. The build checks the catalogue against it."""


__all__ = ['ALL_POLICIES']
