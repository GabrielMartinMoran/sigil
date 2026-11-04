"""Command execution limits."""

DEFAULT_TIMEOUT = 15
"""Timeout per command, in seconds."""

MAX_TIMEOUT = 120
"""Cap of the timeout the model can request."""

MAX_OUTPUT_BYTES = 64 * 1024
"""Output cap per stream (stdout and stderr are counted separately)."""
