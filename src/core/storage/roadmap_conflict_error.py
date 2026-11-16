"""Conflict while updating a stored roadmap."""


class RoadmapConflictError(RuntimeError):
    """The stored plan moved on since the caller read its revision.

    A conflict is a visible failure, not a reason to overwrite: the caller keeps its
    in-memory items and reports the collision instead of winning a race it cannot see.
    """

    def __init__(self, execution_id: str, expected: int, stored: int) -> None:
        self.execution_id = execution_id
        self.expected = expected
        self.stored = stored
        super().__init__(
            f'roadmap revision conflict for execution {execution_id}: '
            f'expected revision {expected}, stored revision {stored}'
        )
