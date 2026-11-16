"""The is_transient helper of the agents layer."""

from __future__ import annotations

from pydantic_ai.exceptions import (
    ModelConnectionError,
    ModelHTTPError,
    ModelQuotaExceededError,
    ModelRateLimitError,
    ModelRequestRejectedError,
    ModelServerError,
    ModelUnavailableError,
)

RETRYABLE_ERRORS = (
    ModelRateLimitError,  # 429
    ModelUnavailableError,  # 503 and its subclass 529 (overloaded)
    ModelServerError,  # 500, 502, 504
    ModelConnectionError,  # dropped connection and transport timeouts
)


RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504, 529})


NON_RETRYABLE_ERRORS = (
    ModelQuotaExceededError,  # 402 and the 429s of quota/billing
    ModelRequestRejectedError,  # 400/422 and their subclasses (auth, 404, context)
)


def is_transient(error: BaseException) -> bool:
    """Whether ``error`` is one of those that can be solved by retrying."""
    # The category-based discard goes first: a permanent error with status 429
    # must not slip through the fallback below.
    if isinstance(error, NON_RETRYABLE_ERRORS):
        return False
    if isinstance(error, RETRYABLE_ERRORS):
        return True
    if isinstance(error, ModelHTTPError):
        # Adapters or custom models that do not classify: the status is enough.
        return error.status_code in RETRYABLE_STATUS_CODES
    return False
