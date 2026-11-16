"""Retries on transient failures of the model provider.

An upstream 502, a proxy 429 or a dropped connection used to bring the whole
turn down: the error reached the UI and the flow stopped. This capability uses
pydantic-ai's ``on_model_request_error`` hook to ask for the response again
while the error is transient and attempts remain.

The retry does not touch the history nor tell the model that there was a failed
attempt: the framework stays on the same step and only repeats the call, so
already executed tools are not run again.

The app runs with ``event_stream_handler``, so pydantic-ai streams
internally and a failure can arrive with ``in_stream=True``. It is retried anyway: if the
stream died before emitting text nothing is seen, and if it died halfway the user
already saw a partial response, but the retry notice in the chat explains why
the turn goes on.
"""

from __future__ import annotations

from src.core.agents.retry_notice import RetryNotice

"""Total attempts per request, counting the first one."""

"""Base delay of the exponential backoff, in seconds."""

"""Cap on the wait between attempts, in seconds."""

"""pydantic-ai categories that are worth another attempt.

Those that do not improve by waiting stay out: exhausted quota or billing,
rejected request (400/401/404/422) and exceeded context window.
"""

"""HTTP statuses worth another attempt when the error comes uncategorized."""

"""Categories that never improve by waiting, even if the status looks retryable.

An exhausted-quota 429 shares its status with a rate-limit 429, but the first one
does not resolve itself: without this filter the five attempts would be wasted.
"""


__all__ = ['RetryNotice']
