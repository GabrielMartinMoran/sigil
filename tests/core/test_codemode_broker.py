"""Broker validates and bounds sandbox-originated tool calls."""

import asyncio
import json

from src.core.codemode.broker import serve_requests


class Writer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        pass


class Dispatcher:
    def __init__(self) -> None:
        self.calls = []

    async def call(self, name, args):
        self.calls.append((name, args))
        if name == 'denied':
            raise PermissionError('denied')
        return {'answer': args['value']}


def test_broker_returns_tool_results_and_errors() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        writer = Writer()
        dispatcher = Dispatcher()
        for request in (
            {'id': 1, 'name': 'read_file', 'args': {'value': 'ok'}},
            {'id': 2, 'name': 'denied', 'args': {}},
        ):
            reader.feed_data(json.dumps(request).encode() + b'\n')
        reader.feed_eof()
        await serve_requests(reader, writer, dispatcher)
        responses = {item['id']: item for item in map(json.loads, writer.buffer.splitlines())}
        assert responses[1]['result'] == {'answer': 'ok'}
        assert responses[2]['error']['type'] == 'PermissionError'
        assert len(dispatcher.calls) == 2

    asyncio.run(run())


def test_broker_rejects_malformed_and_excess_requests() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        writer = Writer()
        dispatcher = Dispatcher()
        reader.feed_data(b'{"id":1,"name":"read_file","args":[]}\n')
        reader.feed_data(b'{"id":2,"name":"read_file","args":{"value":1}}\n')
        reader.feed_eof()
        await serve_requests(reader, writer, dispatcher, max_requests=1)
        responses = [json.loads(line) for line in writer.buffer.splitlines()]
        assert responses[0]['error']['type'] == 'InvalidRequest'
        assert dispatcher.calls == []

    asyncio.run(run())


def test_broker_runs_independent_calls_concurrently() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        writer = Writer()
        both_started = asyncio.Event()
        started = 0

        class ConcurrentDispatcher:
            async def call(self, name, args):
                nonlocal started
                started += 1
                if started == 2:
                    both_started.set()
                await both_started.wait()
                return args['value']

        for number in (1, 2):
            reader.feed_data(
                json.dumps({'id': number, 'name': 'read_file', 'args': {'value': number}}).encode() + b'\n'
            )
        reader.feed_eof()
        await asyncio.wait_for(serve_requests(reader, writer, ConcurrentDispatcher()), timeout=2)
        results = {item['id']: item['result'] for item in map(json.loads, writer.buffer.splitlines())}
        assert results == {1: 1, 2: 2}

    asyncio.run(run())


def test_broker_rejects_oversized_responses() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        writer = Writer()

        class LargeDispatcher:
            async def call(self, name, args):
                return 'x' * (1024 * 1024 + 1)

        reader.feed_data(b'{"id":1,"name":"read_file","args":{}}\n')
        reader.feed_eof()
        await serve_requests(reader, writer, LargeDispatcher())
        assert json.loads(writer.buffer)['error']['type'] == 'ValueError'

    asyncio.run(run())


def test_broker_rejects_duplicate_request_ids() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        writer = Writer()
        dispatcher = Dispatcher()
        request = b'{"id":1,"name":"read_file","args":{"value":"ok"}}\n'
        reader.feed_data(request + request)
        reader.feed_eof()
        await serve_requests(reader, writer, dispatcher)
        responses = [json.loads(line) for line in writer.buffer.splitlines()]
        assert len(dispatcher.calls) == 1
        assert any(item.get('error', {}).get('type') == 'InvalidRequest' for item in responses)

    asyncio.run(run())
