"""Modelos de salida de las tools de skills."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
    """Acuse de carga de un skill (nivel 2).

    El cuerpo del `SKILL.md` **no** viaja acá: se inyecta como bloque de
    instrucciones para que sobreviva a la compactación y no compita por el
    presupuesto del historial. Este resultado sólo confirma la carga y lista los
    recursos que se pueden leer on demand.
    """

    name: str
    description: str
    source: str
    path: str
    resources: list[SkillResourceInfo]
    note: str = Field(description='Where the skill body went and what to do next.')


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
