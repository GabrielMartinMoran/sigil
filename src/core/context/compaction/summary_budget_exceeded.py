"""The SummaryBudgetExceeded value of the context layer."""

from __future__ import annotations


class SummaryBudgetExceeded(ValueError):
    """The auxiliary request cannot safely fit; no model call was made."""
