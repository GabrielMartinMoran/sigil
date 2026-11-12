"""The entry point refuses to become a second executor.

Two facts, both cheap and both one line away from being undone:

- **A refusal is a sentence, not a traceback.** The launcher already reports a typed
  reason, so `_refuse` ends the process with a code a script can read and a line that
  names the way out. A person who cannot act on the message has learned nothing.
- **`src/sigil.py` imports no `Composition`.** Building one is building the engine —
  the agent, the tools, the model router — and a terminal with an engine of its own
  is the second executor on one session that this cut exists to remove. This is a
  test and not a comment because the import is always one line away.

The module docstring names `Composition` on purpose, so the second check is about the
import lines rather than about the word.
"""

from __future__ import annotations

import inspect

import pytest

from src import sigil
from src.sigil import _refuse


def test_a_refusal_exits_non_zero_and_says_what_to_do(capsys: pytest.CaptureFixture[str]) -> None:
    """The launcher's typed reason becomes advice, and the process says it failed."""

    with pytest.raises(SystemExit) as info:
        _refuse('No Sigil service answered at 127.0.0.1:9: refused')

    assert info.value.code == 1
    printed = capsys.readouterr().out
    assert 'Sigil cannot attach to its service' in printed
    assert 'No Sigil service answered' in printed
    assert 'sigil server' in printed, 'the sentence names the way out'
    assert 'Traceback' not in printed


def test_the_entry_point_composes_no_engine() -> None:
    """No `Composition` import, so the in-process turn path is unreachable from here."""

    imports = [line for line in inspect.getsource(sigil).splitlines() if line.startswith(('import ', 'from '))]

    assert not any('bootstrap' in line for line in imports), imports
    assert not hasattr(sigil, 'Composition')
