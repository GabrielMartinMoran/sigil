"""Modelos y errores de las tools de archivos."""

from __future__ import annotations

from pydantic import BaseModel, Field

MAX_READ_BYTES = 256 * 1024
"""Bytes que se leen por defecto cuando no se pide un rango de líneas."""


class FileToolError(Exception):
    """Error de archivo con un mensaje accionable."""


class FileContent(BaseModel):
    """Contenido leído de un archivo."""

    path: str
    size: int
    truncated: bool
    binary: bool
    encoding: str = Field(description='utf-8 for text, base64 for binary content.')
    content: str
    start_line: int | None = None
    end_line: int | None = None
    total_lines: int | None = None


class WriteResult(BaseModel):
    """Resultado de escribir un archivo."""

    path: str
    bytes_written: int
    created: bool
    appended: bool


class PatchResult(BaseModel):
    """Resultado de parchear un archivo."""

    path: str
    mode: str = Field(description='`string` or `lines`.')
    replacements: int
    bytes_before: int
    bytes_after: int
    preview: str
