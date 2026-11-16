"""The describe_tool_error helper of the agents layer."""

from __future__ import annotations

MAX_ERROR_CHARS = 2000


def describe_tool_error(error: BaseException) -> str:
    """Message of a failed tool, with the exception type up front."""
    detail = ' '.join(str(error).split()) or '(no detail)'
    if len(detail) > MAX_ERROR_CHARS:
        detail = detail[: MAX_ERROR_CHARS - 1] + '…'
    return f'{type(error).__name__}: {detail}'
