"""Content-free maintenance records, persisted with the response they preceded."""

from __future__ import annotations

"""Shape of the persisted decision. Bumped when a stored record needs migration."""

LEGACY_POLICY_VERSION = 2
"""Records written before compaction stopped using levels of detail."""

RECORD_KEY = 'sigil_context'
