"""Configuración validada de un servidor MCP."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.engine.mcp.config_models.constants import (
    DEFAULT_INIT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    TOOL_PREFIX_RE,
)


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
        self._check_transport()
        self._check_limits()
        return self

    def _check_transport(self) -> None:
        if self.command and self.url:
            raise ValueError('use either `command` (stdio) or `url` (http), not both')
        if not self.command and not self.url:
            raise ValueError('each server needs `command` (stdio) or `url` (http)')

        if self.command:
            if self.headers:
                raise ValueError('`headers` only applies to `url` servers')
            if self.transport is not None:
                raise ValueError('`transport` only applies to `url` servers')
            return

        if self.args:
            raise ValueError('`args` only applies to `command` servers')
        if self.env:
            raise ValueError('`env` only applies to `command` servers')
        if self.cwd:
            raise ValueError('`cwd` only applies to `command` servers')

    def _check_limits(self) -> None:
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
