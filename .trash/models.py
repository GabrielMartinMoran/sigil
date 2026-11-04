"""Modelos de datos del soporte MCP de Sigil.

La configuración vive en ``.sigil/config.json`` y usa claves en snake_case.
Cada entrada de ``mcp_servers`` se valida por separado: un servidor inválido
no invalida el resto del archivo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# El handshake inicial de un server stdio suele incluir un arranque en frío
# (``npx -y``, ``uvx``), así que el default de 5s de la librería queda corto.
DEFAULT_INIT_TIMEOUT = 30.0
DEFAULT_READ_TIMEOUT = 300.0

SERVER_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
TOOL_PREFIX_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_]*$')


class MCPConfigError(Exception):
    """La configuración de MCP es inválida o falta."""


class MCPServerConfig(BaseModel):
    """Configuración de un servidor MCP.

    Un servidor es stdio (``command``) o HTTP (``url``), nunca ambos.
    """

    model_config = ConfigDict(extra='forbid', frozen=True)

    # --- transporte stdio ---
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    keep_alive: bool = True

    # --- transporte HTTP ---
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    transport: Literal['http', 'sse'] | None = None

    # --- comportamiento ---
    enabled: bool = True
    prefix: str | None = None
    include_instructions: bool = True
    init_timeout: float = DEFAULT_INIT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT
    include_tools: list[str] = Field(default_factory=list)
    exclude_tools: list[str] = Field(default_factory=list)

    @field_validator('command', 'url', 'cwd', mode='before')
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator('prefix', mode='before')
    @classmethod
    def _strip_prefix(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode='after')
    def _check(self) -> MCPServerConfig:
        if self.command and self.url:
            raise ValueError('use either `command` (stdio) or `url` (http), not both')
        if not self.command and not self.url:
            raise ValueError('each server needs `command` (stdio) or `url` (http)')

        if self.command:
            if self.headers:
                raise ValueError('`headers` only applies to `url` servers')
            if self.transport is not None:
                raise ValueError('`transport` only applies to `url` servers')
        else:
            if self.args:
                raise ValueError('`args` only applies to `command` servers')
            if self.env:
                raise ValueError('`env` only applies to `command` servers')
            if self.cwd:
                raise ValueError('`cwd` only applies to `command` servers')

        if self.prefix and not TOOL_PREFIX_RE.match(self.prefix):
            raise ValueError(
                f'invalid `prefix` {self.prefix!r}: use letters, digits and underscores '
                '(it becomes part of the tool name)'
            )
        if self.init_timeout <= 0:
            raise ValueError('`init_timeout` must be greater than 0')
        if self.read_timeout <= 0:
            raise ValueError('`read_timeout` must be greater than 0')
        if self.include_tools and self.exclude_tools:
            raise ValueError('use `include_tools` or `exclude_tools`, not both')
        return self


@dataclass(frozen=True, slots=True)
class MCPServerSpec:
    """Servidor validado, con el contexto del archivo que lo declaró."""

    name: str
    config: MCPServerConfig
    base_dir: Path
    source: Path

    @property
    def transport_kind(self) -> Literal['stdio', 'http']:
        return 'stdio' if self.config.command else 'http'

    @property
    def tool_prefix(self) -> str:
        """Prefijo de las tools; ``None`` usa el nombre del server, ``''`` no prefija."""
        prefix = self.config.prefix
        return self.name if prefix is None else prefix

    @property
    def cwd_path(self) -> Path | None:
        """``cwd`` resuelto: ``~`` expandido y rutas relativas al archivo de config."""
        raw = self.config.cwd
        if not raw:
            return None
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.base_dir / path
        return path.resolve()


# --- modelos de salida de las tools ------------------------------------------


class ServerStatus(BaseModel):
    """Estado de un servidor MCP configurado."""

    name: str
    transport: str = Field(description='stdio or http.')
    enabled: bool
    prefix: str = Field(description="Prefix applied to this server's tool names.")
    tools: list[str] = Field(default_factory=list, description='Tool names as the model sees them.')
    error: str | None = None
    source: str = Field(description='Config file that declared this server.')


class ResourceInfo(BaseModel):
    """Recurso expuesto por un servidor MCP."""

    server: str
    uri: str
    name: str | None = None
    mime_type: str | None = None
    description: str | None = None


class ResourceContent(BaseModel):
    """Contenido de un recurso MCP."""

    server: str
    uri: str
    mime_type: str | None = None
    binary: bool = False
    encoding: str = Field(default='utf-8', description='utf-8 for text, base64 for binary content.')
    content: str


class PromptArgumentInfo(BaseModel):
    """Argumento de un prompt MCP."""

    name: str
    description: str | None = None
    required: bool = False


class PromptInfo(BaseModel):
    """Prompt expuesto por un servidor MCP."""

    server: str
    name: str
    description: str | None = None
    arguments: list[PromptArgumentInfo] = Field(default_factory=list)


class PromptMessageInfo(BaseModel):
    """Mensaje ya renderizado de un prompt MCP."""

    role: str
    content: str


class PromptResultInfo(BaseModel):
    """Resultado de resolver un prompt MCP."""

    server: str
    name: str
    description: str | None = None
    messages: list[PromptMessageInfo] = Field(default_factory=list)
