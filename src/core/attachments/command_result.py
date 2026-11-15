"""Result of a clipboard command."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What a clipboard tool answered. `returncode != 0` is a normal outcome here."""

    returncode: int
    stdout: bytes
    stderr: str = ''

    @property
    def ok(self) -> bool:
        return self.returncode == 0
