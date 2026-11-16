"""The MaintenanceAttempt value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.usage import RunUsage

from src.core.context.compaction.complete_receipt import complete_receipt
from src.core.context.compaction.start_receipt import start_receipt


@dataclass
class MaintenanceAttempt:
    """A run-local auxiliary request; no state is shared between agents."""

    messages: list[ModelMessage]
    usage: RunUsage
    model_name: str
    identifier: str | None = None

    def start(self) -> None:
        self.identifier = start_receipt(self.messages, self.model_name)
        self.usage.requests += 1

    def observe(self, response: ModelResponse) -> None:
        self.usage.incr(response.usage)
        if self.identifier is not None:
            complete_receipt(self.messages, self.identifier, status='responded', response=response)

    def finish(self, status: str, *, error_type: str | None = None) -> None:
        if self.identifier is not None:
            complete_receipt(self.messages, self.identifier, status=status, error_type=error_type)
