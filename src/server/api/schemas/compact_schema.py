"""Manual context compaction request."""

from typing import Literal

from pydantic import BaseModel


class CompactSchema(BaseModel):
    mode: Literal['deterministic', 'llm'] = 'deterministic'
