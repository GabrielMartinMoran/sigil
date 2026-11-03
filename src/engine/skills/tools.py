"""Tools del protocolo Agent Skills expuestas al modelo.

Implementan los tres niveles de *progressive disclosure*:

1. ``list_skills``          — metadatos (nombre + descripción).
2. ``load_skill``           — cuerpo del ``SKILL.md`` + inventario de recursos.
3. ``read_skill_resource``  — lectura puntual de un recurso.
   ``run_skill_script``     — ejecución de un script empaquetado.
"""

from __future__ import annotations

import asyncio
import base64
import os
import shlex
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from src.engine.skills.models import SkillMetadata, SkillResourceError
from src.engine.skills.registry import DEFAULT_MAX_READ_BYTES, SkillRegistry
from src.engine.tools.tool_registry import ToolLogger, ToolRegistrar

DEFAULT_SCRIPT_TIMEOUT = 30


class SkillSummary(BaseModel):
    """Metadatos de un skill instalado."""

    name: str
    description: str
    source: str = Field(description='Where the skill was found: project, global or custom.')
    path: str
    license: str | None = None
    compatibility: str | None = None
    model_invocable: bool = True


class SkillResourceInfo(BaseModel):
    """Archivo empaquetado dentro de un skill."""

    path: str
    size: int
    kind: str = Field(description='scripts, reference, asset or other.')


class LoadedSkill(BaseModel):
    """Resultado de cargar un skill (nivel 2)."""

    name: str
    description: str
    source: str
    path: str
    body: str
    resources: list[SkillResourceInfo]


class ResourceContent(BaseModel):
    """Contenido de un recurso de un skill."""

    skill: str
    path: str
    size: int
    truncated: bool
    binary: bool
    encoding: str = Field(description='utf-8 for text, base64 for binary content.')
    content: str


class ScriptRunResult(BaseModel):
    """Resultado de ejecutar un script empaquetado."""

    skill: str
    script: str
    returncode: int
    stdout: str | None = None
    stderr: str | None = None
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False


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


def _decode(data: bytes) -> tuple[str, bool, str]:
    """Devuelve ``(contenido, es_binario, encoding)``."""
    if b'\x00' in data:
        return base64.b64encode(data).decode('ascii'), True, 'base64'
    try:
        return data.decode('utf-8'), False, 'utf-8'
    except UnicodeDecodeError:
        return base64.b64encode(data).decode('ascii'), True, 'base64'


def _interpreter_for(path: Path) -> list[str]:
    """Elige cómo ejecutar un script según extensión, shebang o permisos.

    La extensión manda sobre el shebang para los lenguajes conocidos: así un
    ``.py`` usa el intérprete de Sigil (con sus dependencias) aunque el script
    declare ``#!/usr/bin/env python3``.
    """
    suffix = path.suffix.lower()
    if suffix == '.py':
        return [sys.executable]
    if suffix in {'.sh', '.bash'}:
        return ['/bin/bash']
    if suffix in {'.js', '.mjs', '.cjs'}:
        return ['node']
    if suffix == '.rb':
        return ['ruby']

    try:
        first_line = path.open('rb').readline(256).decode('utf-8', errors='replace').strip()
    except OSError:
        first_line = ''

    if first_line.startswith('#!'):
        shebang = shlex.split(first_line[2:].strip())
        if shebang:
            return shebang

    if os.access(path, os.X_OK):
        return []

    raise SkillResourceError(
        f'Cannot determine how to run {path.name!r}. Use a .py/.sh/.js/.rb script, '
        'add a shebang and make it executable, or run it through the `run` tool.'
    )


def register_skill_tools(agent, registry: SkillRegistry, logger: ToolLogger | None = None) -> None:
    """Registra las tools de skills en un agente de pydantic-ai."""
    tools = ToolRegistrar(agent, logger)

    @tools.tool
    def list_skills(query: str | None = None) -> list[SkillSummary]:
        """List installed Agent Skills (metadata only).

        Use this to discover which skills are available before starting a task.
        Each result contains the skill name and the description that explains
        when it should be used. Call `load_skill` to read a skill's full
        instructions.

        Args:
            query: Optional case-insensitive filter over name and description.
        """
        # Re-escanear permite descubrir skills instalados durante la sesión.
        registry.refresh()
        skills = registry.list()
        if query:
            needle = query.strip().lower()
            skills = [skill for skill in skills if needle in skill.name.lower() or needle in skill.description.lower()]
        return [_summary(skill) for skill in skills]

    @tools.tool
    def load_skill(name: str) -> LoadedSkill:
        """Load a skill's SKILL.md body and list its bundled resources.

        Call this when a task matches a skill description or the user names a
        skill. The returned body contains the instructions to follow. Resources
        are listed but not loaded; read only the ones you need with
        `read_skill_resource`, and run bundled scripts with `run_skill_script`.

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
            body=skill.body,
            resources=[
                SkillResourceInfo(path=resource.path, size=resource.size, kind=resource.kind)
                for resource in skill.resources
            ],
        )

    @tools.tool
    def read_skill_resource(
        name: str,
        path: str,
        max_bytes: int = DEFAULT_MAX_READ_BYTES,
    ) -> ResourceContent:
        """Read a file bundled inside a skill (references, assets, scripts).

        Paths are relative to the skill directory and cannot escape it. Text
        files are returned as UTF-8; binary files are returned base64-encoded
        with `binary=true`.

        Args:
            name: Skill name.
            path: Resource path relative to the skill directory, e.g.
                `references/schemas.md` or `scripts/run.py`.
            max_bytes: Maximum number of bytes to read (default 256 KiB).
        """
        data, truncated, total_size = registry.read_resource(name, path, max_bytes=max_bytes)
        content, binary, encoding = _decode(data)
        return ResourceContent(
            skill=name,
            path=path,
            size=total_size,
            truncated=truncated,
            binary=binary,
            encoding=encoding,
            content=content,
        )

    @tools.tool
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
        from src.engine.tools.tool_manager import execute_command

        metadata = registry.require(name)
        target = registry.resolve_resource(name, script)
        if not target.is_file():
            raise SkillResourceError(f'Script not found: {script!r}.')

        command = [*_interpreter_for(target), str(target), *(args or [])]
        python_path = os.environ.get('PYTHONPATH')
        extra_env = {
            'SIGIL_SKILL': metadata.name,
            'SIGIL_SKILL_DIR': str(metadata.path),
            'PYTHONPATH': str(metadata.path) + (os.pathsep + python_path if python_path else ''),
        }

        result = await asyncio.to_thread(
            execute_command,
            shlex.join(command),
            timeout_seconds=timeout_seconds,
            cwd=str(metadata.path),
            extra_env=extra_env,
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


__all__ = [
    'LoadedSkill',
    'ResourceContent',
    'ScriptRunResult',
    'SkillResourceInfo',
    'SkillSummary',
    'register_skill_tools',
]
