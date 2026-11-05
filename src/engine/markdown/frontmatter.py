"""The ``---`` YAML ``---`` + body skeleton shared by the Markdown protocols.

Sigil reads two kinds of documents with the same shape: Agent Skills
(``SKILL.md``) and agent profiles (``.sigil/agents/*.md``). Splitting them is
identical in both; only the validation and the meaning of the keys differ, so
that half stays in each protocol module.

The ``label`` argument is what the message calls the document. It exists so
``SKILL.md`` keeps saying ``SKILL.md`` while an agent file can say something of
its own without a second copy of this parser.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

# The frontmatter must open on the first line with ``---`` and close with another
# ``---`` line. We support CRLF line endings and a leading BOM.
_FRONTMATTER_RE = re.compile(
    r'\A---[ \t]*\r?\n(?P<frontmatter>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)',
    re.DOTALL,
)


class FrontmatterError(ValueError):
    """The document does not start with a valid YAML frontmatter block."""


def split_frontmatter(text: str, *, label: str = 'The document') -> tuple[dict[str, Any], str]:
    """Splits the YAML frontmatter from the Markdown body.

    Returns ``(frontmatter, body)``.

    Raises:
        FrontmatterError: When the document does not follow the format.
    """
    text = text.removeprefix('\ufeff')

    if not text.startswith('---'):
        raise FrontmatterError(f'{label} must start with a YAML frontmatter block delimited by `---`.')

    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise FrontmatterError(f'{label} has an invalid or unterminated YAML frontmatter block.')

    raw = match.group('frontmatter')
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f'Invalid YAML in {label} frontmatter: {exc}') from exc

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise FrontmatterError(f'{label} frontmatter must be a YAML mapping.')

    return data, text[match.end() :]
