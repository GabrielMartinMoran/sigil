"""Helpers that used to live in provider_mixin."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from src.core.agents.agent_model_config import AgentModelConfig
from src.core.agents.config_scope import ConfigScope
from src.core.agents.model_defaults_state import ModelDefaultsState
from src.core.agents.profile.models.agent_profile import AgentProfile
from src.core.agents.profile.models.agent_role import AgentRole
from src.ui.state.projected_defaults import ProjectedDefaults
from src.ui.widgets.models.defaults_row import DefaultsRow


def _profile_row(
    profile: AgentProfile, state: ModelDefaultsState, accounts: Mapping[str, str], active: str
) -> DefaultsRow:
    return DefaultsRow(
        target=profile.name,
        label=profile.label,
        summary=_summary(state.effective(profile.name), state.invalid(profile.name), accounts),
        sources=state.sources(profile.name),
        invalid=state.invalid(profile.name),
        active=profile.name == active,
        role=profile.role,
    )


def _summary(config: AgentModelConfig | None, invalid: bool, accounts: Mapping[str, str]) -> str:
    """One entry as a reader recognizes it, or why there is nothing to show.

    ``accounts`` maps an alias to its provider. The config stores the alias, and an
    alias on its own says nothing about who answers the request — so the provider is
    shown too, and its absence is what tells the user the account is gone.
    """
    if invalid:
        return 'invalid entry: see the config errors below'
    if config is None:
        return 'not set'
    provider = accounts.get(config.account)
    served_by = f'{config.account} · {provider}' if provider else config.account
    summary = f'{served_by} · {config.model} · {config.effort}'
    if accounts and provider is None:
        summary += f' · account {config.account!r} is not configured'
    return summary


def _projected_targets(projection: Mapping[str, Any]) -> dict[str | None, Mapping[str, Any]]:
    """Every target the projection lists, keyed the way a row is addressed."""
    targets: dict[str | None, Mapping[str, Any]] = {}
    for entry in projection.get('targets') or ():
        if isinstance(entry, Mapping):
            targets[entry.get('target')] = entry
    return targets


def _projected_sources(entry: Mapping[str, Any] | None) -> frozenset[ConfigScope]:
    """The files that mention one target, as the projection names them."""
    return frozenset(ConfigScope(str(scope)) for scope in (entry or {}).get('sources') or ())


def _projected_config(entry: object) -> AgentModelConfig | None:
    """One projected `{account, model, effort}` as the value a row is built from."""
    if not isinstance(entry, Mapping):
        return None
    account, model, effort = entry.get('account'), entry.get('model'), entry.get('effort')
    if not account or not model or not effort:
        return None
    return AgentModelConfig(str(account), str(model), str(effort))


def _projected_role(row: Mapping[str, Any]) -> AgentRole:
    """The role `/api/state` reports, read as the agent when this build cannot place it.

    An unknown role would otherwise cost the whole list to a `ValueError` over one row
    it could still paint, and the selectable role is what such a row meant before roles
    existed.
    """
    try:
        return AgentRole(str(row.get('role') or AgentRole.AGENT))
    except ValueError:
        return AgentRole.AGENT


def _projected_row(
    target: str | None,
    label: str,
    targets: Mapping[str | None, Mapping[str, Any]],
    accounts: Mapping[str, str],
    *,
    active: bool = False,
    installed: bool = True,
    role: AgentRole = AgentRole.AGENT,
) -> DefaultsRow:
    """One row, with the entry the server resolved for its target.

    The summary is `_summary`'s — the same function the in-process rows use — so the two
    modes cannot word the same entry differently. An unknown entry and an invalid one
    are two different rows on purpose: the second is a value the file holds and the user
    has to clear, the first is nothing to show.
    """
    entry = targets.get(target)
    invalid = bool((entry or {}).get('invalid'))
    return DefaultsRow(
        target=target,
        label=label,
        summary=_summary(
            _projected_config(None if entry is None else entry.get('effective')),
            invalid,
            accounts,
        ),
        sources=_projected_sources(entry),
        invalid=invalid,
        active=active,
        installed=installed,
        role=role,
    )


def defaults_rows(
    state: ModelDefaultsState,
    *,
    profiles: Mapping[str, AgentProfile] | None = None,
    active: str = '',
    providers: Mapping[str, str] | None = None,
) -> tuple[DefaultsRow, ...]:
    """The screen's rows: the default first, then the agents, then the subagents.

    Both roles are listed. `agents.<name>` decides what a profile runs on whether the
    user picks it or a `summon` starts it, so a subagent is a target with a default of
    its own — hiding it would leave its model reachable only by editing the file.

    A profile the config names but the registry no longer has is listed last, and
    marked. An entry for a deleted agent is exactly the one worth clearing, and
    hiding it would leave `agents.<gone>` in the file with no way out from the TUI.
    """
    installed = dict(profiles or {})
    accounts = dict(providers or {})
    agents = sorted((item for item in installed.values() if item.role.selectable), key=lambda item: item.name)
    subagents = sorted((item for item in installed.values() if not item.role.selectable), key=lambda item: item.name)
    orphans = sorted(name for name in state.agent_names() if name not in installed)

    rows = [
        DefaultsRow(
            target=None,
            label='Default for new conversations',
            summary=_summary(state.effective(None), state.invalid(None), accounts),
            sources=state.sources(None),
            invalid=state.invalid(None),
        )
    ]
    rows.extend(_profile_row(profile, state, accounts, active) for profile in (*agents, *subagents))
    rows.extend(
        DefaultsRow(
            target=name,
            label=name,
            summary=_summary(state.effective(name), state.invalid(name), accounts),
            sources=state.sources(name),
            invalid=state.invalid(name),
            installed=False,
        )
        for name in orphans
    )
    return tuple(rows)


def projected_defaults(projection: Mapping[str, Any]) -> ProjectedDefaults:
    """The screen's destination facts, from `GET /api/model-defaults`."""
    files = [
        entry
        for entry in projection.get('files') or ()
        if isinstance(entry, Mapping) and entry.get('scope') and entry.get('path')
    ]
    fixed = projection.get('fixed')
    return ProjectedDefaults(
        destinations=tuple(ConfigScope(str(entry['scope'])) for entry in files),
        paths={ConfigScope(str(entry['scope'])): Path(str(entry['path'])) for entry in files},
        fixed=Path(str(fixed)) if fixed else None,
        errors=tuple(str(error) for error in projection.get('errors') or ()),
    )


def defaults_rows_from_service(
    projection: Mapping[str, Any],
    *,
    accounts: Sequence[Mapping[str, Any]] = (),
    profiles: Sequence[Mapping[str, Any]] = (),
    active: str = '',
) -> tuple[DefaultsRow, ...]:
    """The screen's rows for an attached terminal: the projection, and the profiles.

    Two sources, each answering what only it knows. `/api/model-defaults` says what the
    files hold and which entry wins per target — ``effective``, ``invalid`` and
    ``sources`` are read from it, never recomputed, so the precedence stays where the
    files are. `/api/state` lists the installed profiles, because a profile no file
    mentions is absent from the defaults projection and it is exactly the row a user
    opens this screen to set.

    ``accounts`` is the projection's own account list, and it answers one question: does
    the account this entry names still exist. It is mapped to an empty label on purpose
    — the projection names the account and not the provider — so a configured entry
    prints its alias once instead of repeating it.
    """
    configured = {str(row.get('alias')): '' for row in accounts if isinstance(row, Mapping) and row.get('configured')}
    installed = {str(row.get('name')): row for row in profiles if str(row.get('name') or '')}
    targets = _projected_targets(projection)

    rows = [_projected_row(None, 'Default for new conversations', targets, configured)]
    for selectable in (True, False):
        names = sorted(name for name in installed if _projected_role(installed[name]).selectable is selectable)
        rows.extend(
            _projected_row(
                name,
                name,
                targets,
                configured,
                active=name == active,
                role=_projected_role(installed[name]),
            )
            for name in names
        )
    rows.extend(
        _projected_row(name, name, targets, configured, installed=False)
        for name in sorted(target for target in targets if target is not None and target not in installed)
    )
    return tuple(rows)
