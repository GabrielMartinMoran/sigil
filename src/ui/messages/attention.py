"""Attention message of the terminal."""

from __future__ import annotations

from enum import StrEnum


class Attention(StrEnum):
    FINISHED = 'Turn finished. Your response is ready.'
    FAILED = 'Turn failed. Open Sigil for details.'
    QUESTION = 'A question is waiting for your answer.'
