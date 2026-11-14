"""Ambiguous patch diagnostics stay bounded while preserving overlapping matches."""

from __future__ import annotations

import pytest

from src.engine.files.models.file_tool_error import FileToolError
from src.engine.files.text import apply_string_patch


def test_should_bound_ambiguous_match_locations_without_losing_the_total() -> None:
    with pytest.raises(FileToolError) as error:
        apply_string_patch('match\n' * 50_000, 'match', 'new', False)
    message = str(error.value)
    assert 'appears 50000 times' in message
    assert 'lines 1, 2, 3' in message
    assert '49990 more' in message
    assert len(message) < 400


@pytest.mark.parametrize(
    'text,old,expected,count',
    [('aaaa', 'aa', 'xx', 3), ('ababa', 'aba', 'xba', 2), ('ñ\nñ', 'ñ', 'x\nx', 2)],
)
def test_should_preserve_overlapping_count_in_replace_all(text: str, old: str, expected: str, count: int) -> None:
    assert apply_string_patch(text, old, 'x', True) == (expected, count)


def test_should_preserve_locations_for_small_ambiguous_matches() -> None:
    with pytest.raises(FileToolError, match=r'appears 3 times \(lines 1, 1, 2\)'):
        apply_string_patch('xxx\r\nxx\n', 'xx', 'new', False)


def test_should_preserve_overlap_counts_for_small_needles() -> None:
    from itertools import product

    needles = [''.join(chars) for length in range(1, 4) for chars in product('ab', repeat=length)]
    for length in range(6):
        for characters in product('ab', repeat=length):
            text = ''.join(characters)
            for old in needles:
                count = sum(text.startswith(old, position) for position in range(len(text)))
                if count:
                    assert apply_string_patch(text, old, 'x', True) == (text.replace(old, 'x'), count)
                else:
                    with pytest.raises(FileToolError, match='not found'):
                        apply_string_patch(text, old, 'x', True)
