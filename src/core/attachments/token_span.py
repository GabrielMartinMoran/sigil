"""An image marker found in text."""

from dataclasses import dataclass

from src.core.attachments.token import token


@dataclass(frozen=True, slots=True)
class TokenSpan:
    """A marker found in the text."""

    start: int
    """Offset of its first character in the whole document."""

    end: int
    """Offset just past its last character."""

    number: int

    def contains(self, offset: int) -> bool:
        return self.start <= offset < self.end

    @property
    def text(self) -> str:
        return token(self.number)
