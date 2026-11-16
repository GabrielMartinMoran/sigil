"""Builds the ChatGPT-plan provider for one account."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

OAuthProviderFactory = Callable[[], Any]
