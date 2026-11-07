"""The schema of Sigil's database.

One database for every workspace, in `$SIGIL_HOME/sigil.db`. The DDL below is what a
database created **today** looks like, applied whole. A database written by an older
Sigil carries a lower `PRAGMA user_version` and gets the numbered steps in
`MIGRATIONS` on top: `SCHEMA` is written with `IF NOT EXISTS` and is a no-op for the
tables that already exist, and the columns a step adds are in `SCHEMA` too, so a fresh
file and a migrated one end up identical. That is what makes a schema change a number
and not a rewrite.
"""

from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id              INTEGER PRIMARY KEY,
    path            TEXT    NOT NULL UNIQUE,
    last_session_id TEXT,
    updated_at      TEXT    NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS blobs (
    id         INTEGER PRIMARY KEY,
    sha256     TEXT    NOT NULL UNIQUE,
    kind       TEXT    NOT NULL,
    media_type TEXT,
    size       INTEGER NOT NULL,
    data       BLOB    NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT    PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title        TEXT,
    preview      TEXT    NOT NULL DEFAULT '',
    account_id   TEXT,
    model        TEXT,
    thinking     TEXT,
    window       INTEGER,
    efforts      TEXT    NOT NULL DEFAULT '[]',
    images       INTEGER NOT NULL DEFAULT 1,
    -- What the last request measured of the blocks that are always sent. They are a
    -- property of the conversation and not of the process: `/load` reads them back so
    -- the budget of the next request is the real one. See `ContextMeter`.
    tools_tokens        INTEGER NOT NULL DEFAULT 0,
    instructions_tokens INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
) STRICT, WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS sessions_by_recency   ON sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS sessions_by_workspace ON sessions(workspace_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS agent_executions (
    id               TEXT    PRIMARY KEY,
    session_id       TEXT    NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    parent_id        TEXT             REFERENCES agent_executions(id) ON DELETE CASCADE,
    created_by_turn  TEXT,
    name             TEXT    NOT NULL,
    role             TEXT    NOT NULL,
    profile          TEXT    NOT NULL DEFAULT '',
    depth            INTEGER NOT NULL,
    brief            TEXT    NOT NULL DEFAULT '',
    status           TEXT    NOT NULL,
    mode             TEXT    NOT NULL DEFAULT 'sync',
    model            TEXT    NOT NULL DEFAULT '',
    result           TEXT,
    error            TEXT,
    started_at       REAL    NOT NULL,
    finished_at      REAL,
    turns            INTEGER NOT NULL DEFAULT 0,
    delivered        INTEGER NOT NULL DEFAULT 1,
    channel          TEXT    NOT NULL DEFAULT '{}',
    max_steps        INTEGER,
    budget_exhausted INTEGER NOT NULL DEFAULT 0
) STRICT, WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS executions_by_session ON agent_executions(session_id, depth, started_at);
CREATE INDEX IF NOT EXISTS executions_running    ON agent_executions(session_id) WHERE finished_at IS NULL;

-- One root per session: the invariant, refused by the database.
CREATE UNIQUE INDEX IF NOT EXISTS executions_one_root ON agent_executions(session_id)
    WHERE parent_id IS NULL;

-- And a session may never lose its root: you delete the session, not its root.
-- The EXISTS is load-bearing: SQLite fires this trigger for a delete cascaded
-- from `sessions` too, and by then the session row is already gone.
CREATE TRIGGER IF NOT EXISTS executions_no_orphan BEFORE DELETE ON agent_executions
    WHEN OLD.parent_id IS NULL
     AND EXISTS (SELECT 1 FROM sessions WHERE id = OLD.session_id)
    BEGIN SELECT RAISE(ABORT, 'delete the session, not its root'); END;

-- The turn: the span between the user sending a message and the agent finishing.
-- library_run_id is the identifier pydantic-ai stamps on one run: 1:1 with a
-- turn, and the only place in the schema where the library's word appears.
CREATE TABLE IF NOT EXISTS turns (
    id             TEXT    PRIMARY KEY,
    execution_id   TEXT    NOT NULL REFERENCES agent_executions(id) ON DELETE CASCADE,
    origin         TEXT    NOT NULL,
    started_at     TEXT    NOT NULL,
    finished_at    TEXT,
    outcome        TEXT,
    steps          INTEGER NOT NULL DEFAULT 0,
    cost           REAL,
    library_run_id TEXT,
    CHECK ((finished_at IS NULL) = (outcome IS NULL))
) STRICT, WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS turns_by_execution ON turns(execution_id, started_at);
-- At most one open turn per execution: one writer, refused by the database.
CREATE UNIQUE INDEX IF NOT EXISTS turns_one_open ON turns(execution_id)
    WHERE finished_at IS NULL;

-- An instant a session can be returned to: the conversation, the workspace and the
-- plan. Written when a turn starts; the rollback feature reads it.
CREATE TABLE IF NOT EXISTS checkpoints (
    id             TEXT    PRIMARY KEY,
    session_id     TEXT    NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    execution_id   TEXT    NOT NULL REFERENCES agent_executions(id) ON DELETE CASCADE,
    turn_id        TEXT             REFERENCES turns(id) ON DELETE CASCADE,
    at             TEXT    NOT NULL,
    reason         TEXT    NOT NULL,
    label          TEXT    NOT NULL DEFAULT '',
    tree_commit    TEXT,
    roadmap_blob   INTEGER          REFERENCES blobs(id),
    abandoned_blob INTEGER          REFERENCES blobs(id)
) STRICT, WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS checkpoints_by_session ON checkpoints(session_id, at);
-- One checkpoint per turn; NULL turn_id (before_rollback, manual) is exempt.
CREATE UNIQUE INDEX IF NOT EXISTS checkpoints_per_turn ON checkpoints(turn_id)
    WHERE turn_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS messages (
    execution_id    TEXT    NOT NULL REFERENCES agent_executions(id) ON DELETE CASCADE,
    turn_id         TEXT    NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    kind            TEXT    NOT NULL,
    payload         TEXT    NOT NULL,
    instructions_id INTEGER          REFERENCES blobs(id),
    input_tokens    INTEGER,
    cache_read      INTEGER,
    cache_write     INTEGER,
    output_tokens   INTEGER,
    cost            REAL,
    timestamp       TEXT,
    PRIMARY KEY (execution_id, seq)
) STRICT, WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS messages_usage ON messages(
    execution_id, input_tokens, cache_read, cache_write, output_tokens, cost
);
CREATE INDEX IF NOT EXISTS messages_instructions ON messages(instructions_id)
    WHERE instructions_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS messages_by_turn ON messages(turn_id, seq);

CREATE TABLE IF NOT EXISTS roadmap_items (
    execution_id TEXT    NOT NULL REFERENCES agent_executions(id) ON DELETE CASCADE,
    id           TEXT    NOT NULL,
    position     INTEGER NOT NULL,
    title        TEXT    NOT NULL,
    status       TEXT    NOT NULL,
    notes        TEXT,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    PRIMARY KEY (execution_id, id)
) STRICT, WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS roadmap_order ON roadmap_items(execution_id, position);

CREATE TABLE IF NOT EXISTS accounts (
    id         TEXT PRIMARY KEY,
    alias      TEXT NOT NULL,
    alias_key  TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL,
    variant    TEXT NOT NULL,
    auth       TEXT NOT NULL,
    created_at TEXT,
    last_model TEXT
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT, WITHOUT ROWID;
"""

USER_VERSION = 2
"""The version `SCHEMA` describes.

Steps start above the empty file: a database created today is written whole and its
version is stamped straight away, so a step only ever runs for a database that was
written by an older Sigil.
"""

MIGRATIONS: dict[int, str] = {
    2: (
        # What the last request measured of the blocks that are always sent. A session
        # written before this column existed has measured nothing, and `0` says that
        # honestly: the first request of the resumed conversation fills it in.
        'ALTER TABLE sessions ADD COLUMN tools_tokens INTEGER NOT NULL DEFAULT 0;'
        'ALTER TABLE sessions ADD COLUMN instructions_tokens INTEGER NOT NULL DEFAULT 0;'
    ),
}
"""Numbered steps, each one bringing the file up to its key. Applied in order."""

__all__ = ['MIGRATIONS', 'SCHEMA', 'USER_VERSION']
