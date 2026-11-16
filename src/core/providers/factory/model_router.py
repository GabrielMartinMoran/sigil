"""The ModelRouter value of the providers layer."""

from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.wrapper import WrapperModel


class ModelRouter(WrapperModel):
    """The agent's model, whose target the service replaces between turns.

    ``WrapperModel.__init__`` runs ``infer_model`` on its argument, which rejects
    an already-built ``Model``; seeding ``Model.__init__`` directly and assigning
    ``wrapped`` avoids that and keeps every delegated member working.
    """

    def __init__(self, initial: Model) -> None:
        Model.__init__(self)
        self.wrapped: Model = initial

    def use(self, model: Model) -> None:
        """Replaces the model the next request will use."""
        self.wrapped = model

    @property
    def current(self) -> Model:
        return self.wrapped
