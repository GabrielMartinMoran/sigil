"""Spawning and draining of a non-interactive shell process.

Separated from the public API because they are two distinct responsibilities: here lives
the ``subprocess`` and ``selectors`` handling; in ``runner`` lives the policy
(timeouts, guards, results).
"""

from __future__ import annotations
