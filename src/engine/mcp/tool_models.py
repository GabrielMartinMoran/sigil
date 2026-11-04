"""Modelos de salida de las meta-tools de MCP."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
