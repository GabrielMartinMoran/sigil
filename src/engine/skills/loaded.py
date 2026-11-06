"""Which skills an execution loaded, derived from its own transcript.

The body of a loaded skill does not travel as a tool result: it is injected as an
instruction block. That makes it survive compaction and takes it out of the history
budget, where it would compete with the data.

The set is **not stored**. A `load_skill` call is a `ToolCallPart` in the
execution's transcript, with the name in its arguments, answered by a
`ToolReturnPart` carrying the same `tool_call_id`; that is the whole record. Deriving
it from the log costs 0.17 ms over an 857-message history, and it makes two
operations correct for free that a stored copy breaks silently:

- **Resuming a session**: the copy in memory was never serialized, so a resumed
  conversation kept working with the skills it had loaded at the start and nothing
  said so. The log is the file: the set comes back with it.
- **Rolling back a turn**: truncating the turns truncates the set. No row to delete
  and no cache to invalidate.

A failed load does not count, and the filter is `outcome`, not the part kind:
`ToolErrorRecovery` turns a tool exception into a `ToolReturnPart` with
`outcome='failed'`, so a `load_skill` with an unknown name **looks successful** to
anyone who only checks that a return part exists.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart

LOAD_SKILL_TOOL = 'load_skill'
"""The tool whose calls define the set."""


def loaded_skills(messages: Sequence[ModelMessage]) -> tuple[str, ...]:
    """The skills an execution loaded, in load order and without repeats."""
    calls: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if not isinstance(part, ToolCallPart) or part.tool_name != LOAD_SKILL_TOOL:
                continue
            name = _name_of(part)
            if name:
                calls[part.tool_call_id or ''] = name
    if not calls:
        return ()

    delivered: set[str] = set()
    for message in messages:
        for part in getattr(message, 'parts', ()):
            if (
                isinstance(part, ToolReturnPart)
                and part.tool_name == LOAD_SKILL_TOOL
                and getattr(part, 'outcome', 'success') == 'success'
            ):
                delivered.add(part.tool_call_id or '')

    # Only a call that came back **successfully** counts. A refusal, an unknown
    # name or a tool that raised are all a `ToolReturnPart` with `outcome='failed'`
    # (`ToolErrorRecovery`), so the return part is not enough: the outcome is.
    names: list[str] = []
    for call_id, name in calls.items():
        if call_id not in delivered:
            continue
        if name not in names:
            names.append(name)
    return tuple(names)


def _name_of(part: ToolCallPart) -> str:
    """The `name` argument of a `load_skill` call, or ``''`` when it has none."""
    try:
        args = part.args_as_dict()
    except Exception:  # noqa: BLE001 - a malformed call is not a load
        return ''
    if not isinstance(args, dict):
        return ''
    name = args.get('name')
    return name.strip() if isinstance(name, str) else ''


__all__ = ['LOAD_SKILL_TOOL', 'loaded_skills']
