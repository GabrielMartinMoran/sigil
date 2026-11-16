"""The ProviderPaths value of the providers layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CREDENTIALS_FILE = 'credentials.json'


@dataclass(frozen=True, slots=True)
class ProviderPaths:
    """Where the provider layer keeps what is still a file.

    The accounts and the preferences are rows in the database at `home`; the
    credentials stay a `0600` JSON file, because secrets do not share permissions
    with data that must be world-readable.
    """

    directory: Path

    @property
    def home(self) -> Path:
        """The directory the database lives in: `<SIGIL_HOME>`, one level above."""
        return self.directory.parent

    @property
    def credentials(self) -> Path:
        return self.directory / CREDENTIALS_FILE
