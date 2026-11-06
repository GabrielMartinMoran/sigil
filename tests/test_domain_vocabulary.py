"""The domain's vocabulary, enforced by a test instead of a review.

Two rules, both cheap to check and both easy to break without noticing:

- **`run_id` is the library's word**, and it lives in one module. Everywhere else the
  domain says `turn`. If this fails, some module learned pydantic-ai's vocabulary and
  the boundary is gone.
- **Retired words do not come back.** `round` was a synonym of turn with a different
  meaning in the context strategy; `SkillLoads` was a registry that stopped existing.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / 'src'

TURN_MAP = Path('engine/agents/turn_map.py')
"""The one module allowed to name pydantic-ai's identifier."""


def _python_files() -> list[Path]:
    return sorted(path for path in SRC.rglob('*.py') if '__pycache__' not in path.parts)


def test_only_the_turn_map_names_run_id():
    offenders: list[str] = []
    for path in _python_files():
        relative = path.relative_to(SRC)
        if relative == TURN_MAP:
            continue
        text = path.read_text(encoding='utf-8')
        for number, line in enumerate(text.splitlines(), 1):
            if 'run_id' not in line:
                continue
            # `library_run_id` is the contained name; the bare one is the leak.
            if re.search(r'(?<![a-z_])run_id(?![a-z_])', line):
                offenders.append(f'{relative}:{number}: {line.strip()}')
    assert offenders == [], 'the library vocabulary leaked out of turn_map.py:\n' + '\n'.join(offenders)


def test_the_retired_names_are_gone():
    retired = ('SkillLoads', 'LoadedSkills', 'the round', 'image_rounds', 'keep_recent_turns')
    offenders: list[str] = []
    for path in _python_files():
        text = path.read_text(encoding='utf-8')
        for name in retired:
            if name in text:
                offenders.append(f'{path.relative_to(SRC)}: {name}')
    assert offenders == [], 'retired vocabulary came back:\n' + '\n'.join(offenders)


def test_a_turn_id_is_deterministic_and_scoped_by_the_execution():
    """The scope is what lets a fork exist: two transcripts of one prefix, no clash."""
    from pydantic_ai.messages import ModelRequest, ModelResponse

    from src.engine.agents.turn_map import turn_id_of

    history = [
        ModelRequest(parts=[], run_id='run-1'),
        ModelResponse(parts=[], run_id='run-1'),
        ModelRequest(parts=[], run_id='run-2'),
    ]

    first = turn_id_of(history, 0, scope='exec-a')
    assert turn_id_of(history, 1, scope='exec-a') == first  # same turn
    assert turn_id_of(history, 0, scope='exec-b') != first  # another execution
    assert turn_id_of(history, 2, scope='exec-a') != first  # another turn
    assert turn_id_of(history, 0, scope='exec-a') == first  # stable across calls


def test_a_message_without_an_identifier_gets_its_own_turn():
    """Two messages of one run are one turn; two runs are two."""
    from pydantic_ai.messages import ModelRequest, ModelResponse

    from src.engine.agents.turn_map import turn_id_of

    history = [ModelRequest(parts=[]), ModelResponse(parts=[]), ModelRequest(parts=[])]
    ids = {turn_id_of(history, index, scope='exec') for index in range(3)}

    # No identifiers at all: each message is its own turn, and the ids are distinct.
    assert len(ids) == 3
