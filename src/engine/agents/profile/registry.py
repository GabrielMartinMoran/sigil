"""Index of the available agent profiles and the one in effect.

The registry is the only piece that knows the roots. The UI asks it for the
metadata list, the agent asks it for the body of the active profile, and the
permission capability asks it what the active profile allows.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from src.engine.agents.profile.discovery import default_roots, is_profile_file
from src.engine.agents.profile.models.agent_not_found import AgentNotFound
from src.engine.agents.profile.models.agent_parse_error import AgentParseError
from src.engine.agents.profile.models.agent_profile import AgentProfile
from src.engine.agents.profile.parser import parse_profile

DEFAULT_PROFILE_NAME = 'wizard'
"""The profile in effect when nothing else is chosen or remembered."""


class AgentRegistry:
    """Discovered profiles, keyed by name, plus the active one."""

    def __init__(
        self,
        roots: Sequence[tuple[str, Path]] | None = None,
        *,
        active: str | None = None,
    ) -> None:
        self._roots: list[tuple[str, Path]] = [
            (source, Path(path).expanduser()) for source, path in (roots if roots is not None else default_roots())
        ]
        self._profiles: dict[str, AgentProfile] = {}
        self.errors: list[str] = []
        self._active = ''
        self._discover()
        # A remembered profile that no longer exists must not leave the session
        # without a prompt: it falls back to the default, then to whatever is
        # installed, and only then to "no profile".
        for candidate in (active, DEFAULT_PROFILE_NAME):
            if candidate and self.get(candidate) is not None:
                self._active = str(candidate)
                break
        else:
            first = self.list()
            self._active = first[0].name if first else ''

    # --- discovery ---------------------------------------------------------

    def _discover(self) -> None:
        for source, root in self._roots:
            if not root.is_dir():
                continue
            try:
                entries = sorted(root.iterdir(), key=lambda path: path.name)
            except OSError as exc:
                self.errors.append(f'Cannot list agents directory {root}: {exc}')
                continue

            for entry in entries:
                if not is_profile_file(entry):
                    continue
                try:
                    profile, _ = parse_profile(entry, source=source)
                except AgentParseError as exc:
                    self.errors.append(f'{entry}: {exc}')
                    continue
                # Same name in a higher-precedence root: this one stays shadowed.
                self._profiles.setdefault(profile.name, profile)

    def refresh(self) -> AgentRegistry:
        """Rescans the roots, keeping the active profile if it survived."""
        active = self._active
        self._profiles.clear()
        self.errors.clear()
        self._discover()
        self._active = active if self.get(active) is not None else (self.list()[0].name if self.list() else '')
        return self

    # --- queries -----------------------------------------------------------

    @property
    def roots(self) -> tuple[tuple[str, Path], ...]:
        return tuple(self._roots)

    def __len__(self) -> int:
        return len(self._profiles)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None

    def list(self) -> list[AgentProfile]:
        """Every installed profile, built-ins first, then by name."""
        order = {source: index for index, (source, _) in enumerate(reversed(self._roots))}
        return sorted(self._profiles.values(), key=lambda profile: (order.get(profile.source, 99), profile.name))

    def get(self, name: str) -> AgentProfile | None:
        if not isinstance(name, str):
            return None
        key = name.strip().lower()
        return self._profiles.get(key) if key else None

    def require(self, name: str) -> AgentProfile:
        profile = self.get(name)
        if profile is None:
            available = ', '.join(profile.name for profile in self.list()) or 'none'
            raise AgentNotFound(f"Unknown agent '{name}'. Available agents: {available}.")
        return profile

    # --- the active profile ------------------------------------------------

    @property
    def active(self) -> AgentProfile | None:
        """The profile in effect, or ``None`` when no profile is installed."""
        return self.get(self._active) if self._active else None

    @property
    def active_name(self) -> str:
        return self._active

    def activate(self, name: str) -> AgentProfile:
        """Makes ``name`` the profile in effect and returns it.

        Raises:
            AgentNotFound: When nothing is installed under that name.
        """
        profile = self.require(name)
        self._active = profile.name
        return profile

    def active_body(self) -> str:
        """The Markdown body of the active profile, or ``''``.

        A profile the user is editing can be momentarily broken. The failure is
        recorded once, not on every request: an error list that grows with each
        repaint buries the one message that matters.
        """
        profile = self.active
        if profile is None:
            return ''
        try:
            _, body = parse_profile(profile.profile_file, source=profile.source)
        except AgentParseError as exc:
            message = f'{profile.profile_file}: {exc}'
            if message not in self.errors:
                self.errors.append(message)
            return ''
        return body

    # --- diagnostics -------------------------------------------------------

    def iter_errors(self) -> Iterable[str]:
        return tuple(self.errors)
