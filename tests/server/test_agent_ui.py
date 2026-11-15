"""The agent in the UI: the status bar, `/agent`, `ctrl+a` and the session.

Four behaviours are pinned here, because each of them is a way the feature can
look finished and not be:

- the bar says which agent answers, and it survives a transient message;
- `ctrl+a` opens the picker with the focus in the prompt (it is `home` there);
- `/agent <name>` switches directly, and a name that does not exist is a notice,
  not a crash;
- the agent travels with the session the way the model does.
"""

from __future__ import annotations

from pathlib import Path

from src.engine.agents.profile.registry import AgentRegistry

FRONTMATTER = '---\nname: {name}\ndescription: {description}\n{extra}---\n{body}\n'


def _write(root: Path, name: str, *, description: str = 'An agent.', body: str = 'Body.', extra: str = '') -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f'{name}.md').write_text(
        FRONTMATTER.format(name=name, description=description, body=body, extra=extra),
        encoding='utf-8',
    )


def test_agent_projection_includes_authoritative_permissions(tmp_path: Path) -> None:
    from src.server.views import agent_view

    _write(tmp_path, 'restricted', extra='permissions:\n  write: deny\n')
    profile = AgentRegistry([('project', tmp_path)]).require('restricted')
    assert agent_view(profile, active=True)['permissions'] == profile.permissions.describe()
