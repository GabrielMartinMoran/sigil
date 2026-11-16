"""Compacted edits retain the operations that explain the change."""

from src.core.tooling.compaction_policies import ALL_COMPACTORS


def test_edit_compaction_retains_operation_types_and_ranges() -> None:
    args = {
        'path': 'example.py',
        'operations': [
            {'type': 'replace', 'old': 'a' * 10000, 'new': 'b' * 10000},
            {'type': 'replace_lines', 'start_line': 2, 'end_line': 3, 'new': 'c' * 10000},
        ],
    }
    compact = ALL_COMPACTORS['write_file'].compact_input(args)
    assert compact['path'] == 'example.py'
    assert [item['type'] for item in compact['operations']] == ['replace', 'replace_lines']
    assert compact['operations'][1]['start_line'] == 2
    assert len(compact['operations'][0]['old']) < 10000
