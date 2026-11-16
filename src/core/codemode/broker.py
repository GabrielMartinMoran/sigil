"""Bounded JSON-lines broker for sandbox-originated nested tool requests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any, Protocol

from pydantic_core import to_jsonable_python

MAX_FRAME_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_REQUESTS = 64
MAX_IN_FLIGHT = 8


class Dispatcher(Protocol):
    async def call(self, name: str, args: dict[str, Any]) -> Any: ...


class Writer(Protocol):
    def write(self, data: bytes) -> None: ...
    async def drain(self) -> None: ...


def _parse(frame: bytes) -> tuple[int | str, str, dict[str, Any]]:
    value = json.loads(frame)
    if not isinstance(value, dict) or set(value) != {'id', 'name', 'args'}:
        raise ValueError('Expected id, name, and args')
    call_id, name, args = value['id'], value['name'], value['args']
    if isinstance(call_id, bool) or not isinstance(call_id, (str, int)) or len(str(call_id)) > 128:
        raise ValueError('Invalid id')
    if not isinstance(name, str) or not name or len(name) > 256 or not isinstance(args, dict):
        raise ValueError('Invalid tool name or arguments')
    return call_id, name, args


def _encode(response: Mapping[str, Any]) -> bytes:
    data = json.dumps(to_jsonable_python(response), ensure_ascii=True, separators=(',', ':')).encode() + b'\n'
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError('Tool result exceeds the response size limit')
    return data


async def serve_requests(
    reader: asyncio.StreamReader, writer: Writer, dispatcher: Dispatcher, *, max_requests: int = MAX_REQUESTS
) -> None:
    """Serve bounded calls; the owner closes the channel and cancels on sandbox exit."""
    if not 1 <= max_requests <= MAX_REQUESTS:
        raise ValueError('Invalid request limit')
    write_lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(MAX_IN_FLIGHT)

    async def send(response: Mapping[str, Any]) -> None:
        data = _encode(response)
        async with write_lock:
            writer.write(data)
            await writer.drain()

    async def dispatch(call_id: int | str, name: str, args: dict[str, Any]) -> None:
        async with semaphore:
            try:
                result = await dispatcher.call(name, args)
                response = {'id': call_id, 'result': result}
                _encode(response)
            except Exception as exc:  # noqa: BLE001 - transport boundary returns individual call failures
                response = {'id': call_id, 'error': {'type': type(exc).__name__, 'message': str(exc)[:2048]}}
            await send(response)

    seen: set[int | str] = set()
    async with asyncio.TaskGroup() as tasks:
        for _ in range(max_requests):
            try:
                frame = await reader.readline()
            except ValueError:
                await send({'id': None, 'error': {'type': 'InvalidRequest', 'message': 'Request frame too large'}})
                break
            if not frame:
                break
            if len(frame) > MAX_FRAME_BYTES:
                await send({'id': None, 'error': {'type': 'InvalidRequest', 'message': 'Request frame too large'}})
                break
            try:
                call_id, name, args = _parse(frame)
            except ValueError, UnicodeDecodeError:
                await send({'id': None, 'error': {'type': 'InvalidRequest', 'message': 'Invalid request'}})
                continue
            if call_id in seen:
                await send({'id': None, 'error': {'type': 'InvalidRequest', 'message': 'Duplicate request id'}})
                continue
            seen.add(call_id)
            tasks.create_task(dispatch(call_id, name, args))
