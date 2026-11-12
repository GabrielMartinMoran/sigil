"""Index of the available agent profiles and the one in effect.

The registry is the only piece that knows the roots. The UI asks it for the
metadata list, the agent asks it for the body of the active profile, and the
permission capability asks it what the active profile allows.

It also keeps the two roles apart. ``list()`` is every installed profile, and
that is what the summon list is drawn from; ``agents()`` is the ones the user can
select and ``subagents()`` the ones only a ``summon`` can start. Selecting a
subagent as the session's agent is refused: the agent at the root of a session is
the one a person talks to, and a profile written to answer a single task is a bad
conversation partner.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
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
        cwd: str | Path | None = None,
    ) -> None:
        self._default_roots = roots is None
        self.on_rebind: Callable[[], None] | None = None
        self._roots: list[tuple[str, Path]] = [
            (source, Path(path).expanduser()) for source, path in (roots if roots is not None else default_roots(cwd))
        ]
        self._profiles: dict[str, AgentProfile] = {}
        self.errors: list[str] = []
        self._active = ''
        self._discover()
        # A remembered profile that no longer exists must not leave the session
        # without a prompt: it falls back to the default, then to whatever is
        # installed, and only then to "no profile".
        for candidate in (active, DEFAULT_PROFILE_NAME):
            if candidate and (profile := self.get(candidate)) is not None and profile.role.selectable:
                self._active = profile.name
                break
        else:
            # Only an agent can be the session's: a directory with nothing but
            # subagents leaves the session without a profile, which is the same
            # state as an empty one and is painted the same way.
            selectable = self.agents()
            self._active = selectable[0].name if selectable else ''

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
        current = self.get(active) if active else None
        if current is not None and current.role.selectable:
            self._active = current.name
        else:
            selectable = self.agents()
            self._active = selectable[0].name if selectable else ''
        return self

    def rebind(self, root: str | Path) -> None:
        """Reload project profiles without replacing this registry."""
        if not self._default_roots:
            return
        self._roots = default_roots(root)
        self.refresh()
        if self.on_rebind is not None:
            self.on_rebind()

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

    def agents(self) -> list[AgentProfile]:
        """The profiles the user can select: the ones with the ``agent`` role."""
        return [profile for profile in self.list() if profile.role.selectable]

    def subagents(self) -> list[AgentProfile]:
        """The profiles a ``summon`` can start, whatever the permissions allow."""
        return [profile for profile in self.list() if not profile.role.selectable]

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

    def require_subagent(self, name: str) -> AgentProfile:
        """The profile a ``summon`` names, which has to exist and be a subagent.

        Raises:
            AgentNotFound: When nothing is installed under that name, or when the
                profile exists but is an agent: an agent cannot be started as a
                subagent of a session whose root it could be itself.
        """
        profile = self.get(name)
        if profile is None:
            available = ', '.join(candidate.name for candidate in self.subagents()) or 'none'
            raise AgentNotFound(f"Unknown subagent '{name}'. Available subagents: {available}.")
        if profile.role.selectable:
            raise AgentNotFound(
                f"'{profile.name}' is an agent, not a subagent: add `role: subagent` to its profile to summon it."
            )
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
        if not profile.role.selectable:
            raise AgentNotFound(
                f"'{profile.name}' is a subagent: only an agent can be the agent of a session. "
                'An agent summons it with the `summon` tool.'
            )
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
