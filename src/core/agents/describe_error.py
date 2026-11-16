"""The describe_error helper of the agents layer."""

from __future__ import annotations


def describe_error(error: BaseException) -> str:
    """One-line summary of the error, fit to show in the chat."""
    status = getattr(error, 'status_code', None)
    detail = _one_line(getattr(error, 'body', None)) or _one_line(str(error)) or type(error).__name__
    if status is None:
        return f'{type(error).__name__} · {detail}'
    return f'HTTP {status} · {detail}'


def _one_line(value: object, limit: int = 140) -> str:
    if value is None:
        return ''
    text = ' '.join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + '…'
