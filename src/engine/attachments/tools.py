"""`view_image`: hand the model back a picture the history stopped carrying.

An image costs what its pixels cost, so only the most recent ones travel with the
request. The older ones leave their id behind, and this tool turns that id back into
the picture.

Returning a `BinaryImage` from a tool is not the same as returning a link: pydantic-ai
maps multimodal tool results natively —Anthropic as an image block inside the
`tool_result`, OpenAI likewise— so the model really does see the image, not a path
to it.

`ToolReturn` is what makes that possible: `return_value` is the tool's answer, and
`content` is sent as its own prompt part next to it, which is where the pixels go
while the answer stays a short line of text.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.messages import BinaryImage

from src.engine.attachments.library import find_image
from src.engine.files.models.file_tool_error import FileToolError
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.permission import ToolGroup, ToolPermission
from src.engine.tooling.registrar import ToolRegistrar

_VIEW = ToolPermission(groups=(ToolGroup.READ,), resource='image_id')
"""Reading a picture the session already has: it touches no path and no network."""


def register_image_tools(agent: Any, logger: ToolLogger | None = None) -> None:
    """Registers the image tool on a pydantic-ai agent."""
    tools = ToolRegistrar(agent, logger)

    @tools.context_tool(permission=_VIEW)
    def view_image(ctx: RunContext[Any], *, image_id: str) -> ToolReturn:
        """Look at an image that was sent earlier in this session.

        Older pictures stop travelling with the conversation to keep the context
        small; what stays is their id, written in the message as `img_...`. Use this
        when a message mentions an image that is no longer attached, and you need to
        see it before answering.

        Args:
            image_id: The `img_...` id written where the image used to be.
        """
        found = find_image(ctx.messages, image_id)
        if found is None:
            raise FileToolError(
                f'No image with id {image_id!r} in this session. '
                'The ids are the `img_...` values the messages mention; an empty history has none.'
            )
        return ToolReturn(
            return_value=f'image {image_id} ({found.media_type})',
            content=[
                BinaryImage(
                    data=found.data,
                    media_type=found.media_type,
                    identifier=found.identifier,
                )
            ],
        )
