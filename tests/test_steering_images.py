"""A message typed mid-turn, with an image attached.

The steering path had one trap worth a test of its own: `AgentRun.enqueue` takes
one item per argument and gathers adjacent content into a single `UserPromptPart`.
Passing the multimodal list as one argument turns the prompt into a list *of* a
list, which pydantic-ai rejects — and the rejection happens inside a worker, so
the message would be lost with a notice nobody reads.

The test drives a real run and checks what the model was handed: the text with
its marker, and the image beside it, in the same request.
"""

from __future__ import annotations

import asyncio
import io

from PIL import Image
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.attachments.image_attachment import ImageAttachment
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (24, 24), color='#3465a4').save(buffer, format='PNG')
    return buffer.getvalue()


def _attachment(number: int) -> ImageAttachment:
    return ImageAttachment(number=number, data=_png(), media_type='image/png', width=24, height=24)


async def _wait(pilot, predicate, attempts: int = 80) -> bool:
    for _ in range(attempts):
        await pilot.pause()
        if predicate():
            return True
    return False


def test_an_image_pasted_mid_turn_lands_in_the_running_history(tmp_path):
    async def inner() -> None:
        release = asyncio.Event()
        started = asyncio.Event()

        agent = Agent(TestModel(call_tools=['wait_for_it'], custom_output_text='done'), instructions='x')

        @agent.tool_plain
        async def wait_for_it() -> str:
            started.set()
            await release.wait()
            return 'released'

        store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')
        app = SigilApp(agent=agent, store=store, session=store.create_session())

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press('g', 'o')
            await pilot.press('enter')
            assert await _wait(pilot, started.is_set)

            # The turn is blocked on the tool; this is the steering path.
            app.steer('mirá esto', attachments=(_attachment(1),))
            release.set()

            assert await _wait(pilot, lambda: not app._turn_active)

        # The second user request is the steered one, and it carries the image.
        requests = [
            message
            for message in app._session.messages
            if getattr(message, 'parts', None) and message.parts[0].part_kind == 'user-prompt'
        ]
        steered = [request for request in requests if isinstance(request.parts[0].content, list)]
        assert steered, [(request.parts[0].part_kind, request.parts[0].content) for request in requests]
        content = steered[0].parts[0].content
        assert content[0] == 'mirá esto'
        assert any(getattr(item, 'media_type', '') == 'image/png' for item in content)
        # Not a list of a list: `enqueue` was given one item per argument.
        assert not any(isinstance(item, list) for item in content)

    asyncio.run(inner())
