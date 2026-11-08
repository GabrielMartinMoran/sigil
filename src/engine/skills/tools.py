"""Agent Skills protocol tools exposed to the model.

They implement the three levels of *progressive disclosure*:

1. ``list_skills``          : metadata (name + description).
2. ``load_skill``           : ``SKILL.md`` body + resource inventory.
3. ``read_skill_resource``  : targeted read of one resource.
   ``run_skill_script``     : execution of a bundled script.
"""

from __future__ import annotations

import asyncio
import os
import shlex
from typing import Any

from src.engine.shell.runner import execute_command
from src.engine.skills.discovery import DEFAULT_MAX_READ_BYTES
from src.engine.skills.models.skill_metadata import SkillMetadata
from src.engine.skills.models.skill_resource_error import SkillResourceError
from src.engine.skills.registry import SkillRegistry
from src.engine.skills.scripts import interpreter_for
from src.engine.skills.tool_models.loaded_skill import LoadedSkill
from src.engine.skills.tool_models.resource_content import ResourceContent
from src.engine.skills.tool_models.script_run_result import ScriptRunResult
from src.engine.skills.tool_models.skill_resource_info import SkillResourceInfo
from src.engine.skills.tool_models.skill_summary import SkillSummary
from src.engine.tooling.catalog import ToolCatalog
from src.engine.tooling.lod_policies import (
    LIST_SKILLS_LOD,
    LOAD_SKILL_LOD,
    READ_SKILL_RESOURCE_LOD,
    RUN_SKILL_SCRIPT_LOD,
)
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.permission import ToolGroup, ToolPermission
from src.engine.tooling.registrar import ToolRegistrar

_SKILLS = ToolPermission(groups=(ToolGroup.SKILLS,))
_SKILL_RESOURCE = ToolPermission(groups=(ToolGroup.SKILLS,), resource='name')
_SKILL_SCRIPT = ToolPermission(groups=(ToolGroup.SKILLS, ToolGroup.RUN), resource='name')

DEFAULT_SCRIPT_TIMEOUT = 30


def _summary(metadata: SkillMetadata) -> SkillSummary:
    return SkillSummary(
        name=metadata.name,
        description=metadata.description,
        source=metadata.source,
        path=str(metadata.path),
        license=metadata.license,
        compatibility=metadata.compatibility,
        model_invocable=metadata.model_invocable,
    )


def _is_binary(data: bytes, *, truncated: bool) -> bool:
    if b'\x00' in data:
        return True
    try:
        data.decode('utf-8')
    except UnicodeDecodeError as error:
        return not (truncated and error.reason == 'unexpected end of data' and error.end == len(data))
    return False


def _script_env(metadata: SkillMetadata) -> dict[str, str]:
    """Script environment: the skill can import itself."""
    python_path = os.environ.get('PYTHONPATH')
    return {
        'SIGIL_SKILL': metadata.name,
        'SIGIL_SKILL_DIR': str(metadata.path),
        'PYTHONPATH': str(metadata.path) + (os.pathsep + python_path if python_path else ''),
    }


def _matches(skill: SkillMetadata, needle: str) -> bool:
    return needle in skill.name.lower() or needle in skill.description.lower()


def register_skill_tools(
    agent: Any,
    registry: SkillRegistry,
    logger: ToolLogger | None = None,
    catalog: ToolCatalog | None = None,
) -> None:
    """Registers the skills tools on a pydantic-ai agent.

    ``load_skill`` does not write to a registry: the call itself is the record, and
    the set of loaded skills is derived from the transcript when the instructions
    are rendered (see ``skills.loaded.loaded_skills``). Nothing to keep in step, and
    nothing to restore on resume.
    """
    tools = ToolRegistrar(agent, logger, catalog)

    @tools.tool(permission=_SKILLS, lod=LIST_SKILLS_LOD)
    def list_skills(query: str | None = None) -> list[SkillSummary]:
        """List installed Agent Skills (metadata only).

        Use this to discover which skills are available before starting a task.
        Each result contains the skill name and the description that explains
        when it should be used. Call `load_skill` to read a skill's full
        instructions.

        Args:
            query: Optional case-insensitive filter over name and description.
        """
        # Rescanning allows discovering skills installed during the session.
        registry.refresh()
        skills = registry.list()
        if query:
            needle = query.strip().lower()
            skills = [skill for skill in skills if _matches(skill, needle)]
        return [_summary(skill) for skill in skills]

    @tools.tool(permission=_SKILL_RESOURCE, lod=LOAD_SKILL_LOD)
    def load_skill(name: str) -> LoadedSkill:
        """Load a skill and list its bundled resources.

        Call this when a task matches a skill description or the user names a
        skill. The skill's instructions are injected into your system prompt
        under "Loaded skills" on the next step — they are not repeated in this
        result. Resources are listed but not loaded; read only the ones you need
        with `read_skill_resource`, and run bundled scripts with
        `run_skill_script`.

        Args:
            name: Skill name as returned by `list_skills`.
        """
        if registry.get(name) is None:
            registry.refresh()
        skill = registry.load(name)
        return LoadedSkill(
            name=skill.metadata.name,
            description=skill.metadata.description,
            source=skill.metadata.source,
            path=str(skill.metadata.path),
            resources=[
                SkillResourceInfo(path=resource.path, size=resource.size, kind=resource.kind)
                for resource in skill.resources
            ],
            note=(
                f'The instructions of `{skill.metadata.name}` are now in your system prompt '
                'under "Loaded skills". Do not call `load_skill` again for it. Read bundled '
                'resources on demand with `read_skill_resource`.'
            ),
        )

    @tools.tool(permission=_SKILL_RESOURCE, lod=READ_SKILL_RESOURCE_LOD)
    def read_skill_resource(
        name: str,
        path: str,
        max_bytes: int = DEFAULT_MAX_READ_BYTES,
    ) -> ResourceContent:
        """Read a file bundled inside a skill (references, assets, scripts).

        Paths are relative to the skill directory and cannot escape it. Text
        is injected into the loaded skills instructions on the next request.
        Binary content is not included in the instructions or this result.

        Args:
            name: Skill name.
            path: Resource path relative to the skill directory, e.g.
                `references/schemas.md` or `scripts/run.py`.
            max_bytes: Maximum number of bytes to read (default 256 KiB).
        """
        data, truncated, total_size = registry.read_resource(name, path, max_bytes=max_bytes)
        binary = _is_binary(data, truncated=truncated)
        note = (
            'Binary content is not available in the prompt or this result.'
            if binary
            else 'The text is available in the Loaded skills instruction block on the next request.'
        )
        return ResourceContent(
            skill=name,
            path=path,
            size=total_size,
            truncated=truncated,
            binary=binary,
            note=note,
        )

    @tools.tool(permission=_SKILL_SCRIPT, lod=RUN_SKILL_SCRIPT_LOD)
    async def run_skill_script(
        name: str,
        script: str,
        args: list[str] | None = None,
        timeout_seconds: int = DEFAULT_SCRIPT_TIMEOUT,
    ) -> ScriptRunResult:
        """Run a script bundled inside a skill.

        The script runs with the skill directory as its working directory, so
        relative paths and package imports used by the skill keep working.
        Prefer this over copying or reimplementing a skill's scripts.

        Args:
            name: Skill name.
            script: Script path relative to the skill directory, e.g.
                `scripts/aggregate_benchmark.py`.
            args: Optional list of command-line arguments.
            timeout_seconds: Execution timeout in seconds (1-120).
        """
        metadata = registry.require(name)
        target = registry.resolve_resource(name, script)
        if not target.is_file():
            raise SkillResourceError(f'Script not found: {script!r}.')

        command = [*interpreter_for(target), str(target), *(args or [])]
        result = await asyncio.to_thread(
            execute_command,
            shlex.join(command),
            timeout_seconds=timeout_seconds,
            cwd=str(metadata.path),
            extra_env=_script_env(metadata),
            check_self_invocation=False,
        )
        return ScriptRunResult(
            skill=metadata.name,
            script=script,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
        )
