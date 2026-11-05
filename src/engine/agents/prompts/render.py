"""The base prompt, rendered for what the model and the agent can actually do.

The prompt is one file of markdown, and two of its sections describe tools. Telling a
model about `read_file` when the active profile denies `read`, or about images when
the route is text-only, is worse than saying nothing: the model plans around a
capability it does not have, and the plan fails at the first call.

So the sections are conditional. The markup is an HTML comment, which is invisible in
every markdown renderer, so the file stays readable and diffable as prose:

    <!-- if: image_input -->
    ## Images in the prompt
    ...
    <!-- endif -->

A trailing marker gates a single line, which is what a bullet in a list needs:

    - **`read_file`** — inspect files. <!-- if: read_file -->

A block is kept only when **every** feature it names is available. A name is either a
tool the model will see on this request or a model feature such as `image_input`.

No template engine: a dependency that turns the prompt into something that is no
longer markdown, to express "include this or not", costs more than it explains.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_BLOCK = re.compile(r'^<!--\s*if:\s*(?P<names>[^>]*?)\s*-->\s*$')
_END = re.compile(r'^<!--\s*endif\s*-->\s*$')
_LINE = re.compile(r'\s*<!--\s*if:\s*(?P<names>[^>]*?)\s*-->\s*$')


def _names(raw: str) -> frozenset[str]:
    return frozenset(name for name in re.split(r'[\s,]+', raw.strip()) if name)


def render(template: str, available: Iterable[str] | None) -> str:
    """The prompt with only the sections the caller can use.

    Args:
        template: The markdown with its optional markers.
        available: Tool names and model features this request has. ``None`` keeps
            every section, which is what an embedded agent or a test wants.

    Returns:
        The rendered prompt. Markers never reach the model.

    Raises:
        ValueError: When a block is opened and never closed, or closed and never
            opened. A silently swallowed marker would ship half a section.
    """
    have = frozenset() if available is None else frozenset(available)
    lines = template.splitlines()
    out: list[str] = []
    # A stack, so a nested section is possible later without rewriting this.
    open_blocks: list[bool] = []

    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if match := _BLOCK.match(stripped):
            required = _names(match.group('names'))
            open_blocks.append(available is None or required <= have)
            continue
        if _END.match(stripped):
            if not open_blocks:
                raise ValueError(f'`endif` without `if` on line {number}')
            open_blocks.pop()
            continue

        enabled = all(open_blocks)
        if match := _LINE.search(line):
            required = _names(match.group('names'))
            if available is not None and not required <= have:
                continue
            line = line[: match.start()].rstrip()
        if enabled:
            out.append(line)

    if open_blocks:
        raise ValueError('a section was opened with `if:` and never closed with `endif`')

    return _collapse_blank_lines('\n'.join(out))


def _collapse_blank_lines(text: str) -> str:
    """One blank line between sections, none at the edges.

    Dropping a section leaves the blank lines that separated it, and a prompt full of
    holes is a prompt the model reads as sloppy formatting.
    """
    collapsed = re.sub(r'\n{3,}', '\n\n', text)
    return collapsed.strip() + '\n'
