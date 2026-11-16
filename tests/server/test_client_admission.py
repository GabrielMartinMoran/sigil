"""HTTP admission and the shutdown decision share the same gate."""

import asyncio

import httpx
from fastapi import FastAPI

from src.server.api.client_admission_middleware import ClientAdmissionMiddleware
from src.server.runtime.client_presence import ClientPresence


def test_inflight_request_prevents_shutdown_and_later_requests_are_refused() -> None:
    async def go() -> None:
        presence = ClientPresence(ttl=60)
        entered = asyncio.Event()
        release = asyncio.Event()
        app = FastAPI()
        app.add_middleware(ClientAdmissionMiddleware, presence=presence)

        @app.post('/slow')
        async def slow() -> dict[str, bool]:
            entered.set()
            await release.wait()
            return {'ok': True}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            request = asyncio.create_task(client.post('/slow'))
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                assert not presence.begin_shutdown(lambda: False)
                release.set()
                assert (await asyncio.wait_for(request, timeout=2)).status_code == 200
                assert presence.begin_shutdown(lambda: False)
                assert (await client.post('/slow')).status_code == 503
            finally:
                release.set()
                await asyncio.gather(request, return_exceptions=True)

    asyncio.run(go())
