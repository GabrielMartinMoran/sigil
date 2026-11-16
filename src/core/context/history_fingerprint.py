"""The history_fingerprint helper of the context layer."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import replace

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter


def history_fingerprint(messages: Sequence[ModelMessage]) -> str:
    """Digest only sent parts and roles, excluding usage and application metadata."""
    envelopes = [replace(message, metadata=None) for message in messages]
    rows = ModelMessagesTypeAdapter.dump_python(envelopes, mode='json')
    parts = [(row['kind'], part) for row in rows for part in row['parts']]
    serialized = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()
