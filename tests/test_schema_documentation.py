"""Promote the drift check to a test, so the document cannot rot silently."""

from __future__ import annotations

import re
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.engine.storage.schema import SCHEMA, USER_VERSION

DOC = Path(__file__).resolve().parent.parent / 'docs' / 'storage.md'


def _documented_ddl() -> str:
    blocks = re.findall(r'```sql\n(.*?)```', DOC.read_text(encoding='utf-8'), re.DOTALL)
    assert blocks, 'the design page publishes no DDL'
    # The pragmas of the appendix are connection settings, not schema.
    return '\n'.join(line for line in blocks[-1].splitlines() if not line.strip().startswith('PRAGMA'))


def _describe(sql: str) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    connection = sqlite3.connect(Path(tempfile.mkdtemp()) / 'compare.db', isolation_level=None)
    connection.executescript(sql)
    objects = {
        kind: {
            row[0]
            for row in connection.execute(
                f"SELECT name FROM sqlite_master WHERE type='{kind}' AND name NOT LIKE 'sqlite_%'"
            )
        }
        for kind in ('table', 'index', 'trigger')
    }
    columns = {
        table: [row[1] for row in connection.execute(f'PRAGMA table_info({table})')]
        for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    connection.close()
    return objects, columns


@pytest.mark.parametrize('kind', ['table', 'index', 'trigger'])
def test_the_document_publishes_the_objects_the_code_creates(kind: str):
    documented, _ = _describe(_documented_ddl())
    code, _ = _describe(SCHEMA)

    assert documented[kind] - code[kind] == set(), f'{kind} only in the document'
    assert code[kind] - documented[kind] == set(), f'{kind} only in the code'


def test_the_document_publishes_the_columns_the_code_creates():
    """A published column that does not exist is worse than no column at all."""
    _, documented = _describe(_documented_ddl())
    _, code = _describe(SCHEMA)

    drift = {
        table: (documented.get(table, []), code.get(table, []))
        for table in set(documented) | set(code)
        if documented.get(table, []) != code.get(table, [])
    }
    assert drift == {}, f'columns drifted between the document and the code: {drift}'


def test_the_schema_is_applied_whole_and_versioned():
    connection = sqlite3.connect(Path(tempfile.mkdtemp()) / 'version.db', isolation_level=None)
    connection.executescript(SCHEMA)
    connection.execute(f'PRAGMA user_version = {USER_VERSION}')

    assert connection.execute('PRAGMA user_version').fetchone()[0] == USER_VERSION
    # Applying it twice must be harmless: every statement is IF NOT EXISTS.
    connection.executescript(SCHEMA)
    connection.close()
