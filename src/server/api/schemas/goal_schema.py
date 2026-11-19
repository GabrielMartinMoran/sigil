"""The body of a ``/goal`` command."""

from pydantic import BaseModel


class GoalSchema(BaseModel):
    """The raw argument after ``/goal``, parsed by the same function as in process.

    One parser decides what ``--yolo``, ``stop`` and ``status`` mean. The route does
    not repeat those spellings in a second vocabulary that could drift.
    """

    argument: str = ''
