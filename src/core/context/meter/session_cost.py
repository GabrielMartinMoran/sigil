"""The SessionCost value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SessionCost:
    """The bill of a session, and how much of it is known.

    ``total`` is ``None`` only when there are requests and none of them could be
    priced: a real zero (nothing sent yet) is ``Decimal(0)``.
    """

    total: Decimal | None = None
    reported: int = 0
    """Requests priced by the provider itself: the amount it is going to charge."""
    estimated: int = 0
    """Requests priced from a table: what they cost at the published rate."""
    unpriced: int = 0
    """Requests with usage that no table and no provider could price."""
    unmeasured: int = 0
    """Requests with no usage at all: an old session, or a provider that omits it."""

    @property
    def priced(self) -> int:
        """Requests inside ``total``."""
        return self.reported + self.estimated

    @property
    def requests(self) -> int:
        """Requests the session sent, priced or not."""
        return self.priced + self.unpriced + self.unmeasured

    @property
    def approximate(self) -> bool:
        """Part of the total is a list price rather than a charge."""
        return self.estimated > 0

    @property
    def partial(self) -> bool:
        """Something was left out, so the total is a floor."""
        return self.unpriced > 0 or self.unmeasured > 0

    @property
    def exact(self) -> bool:
        """Every request was priced, and by the provider itself."""
        return self.priced > 0 and not self.approximate and not self.partial
