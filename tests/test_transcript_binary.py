"""What a transcript item carries: text, and a description of any binary payload.

The bug this file pins down: `str()` over a `UserPromptPart` whose content is a list
of text and images produced the repr of the list, escaped bytes included. A user who
pasted a screenshot saw `\\x89PNG\\r\\n\\x1a\\n` in the middle of their own sentence, and
the response carried the whole payload twice.

The terminal client never had that bug because it splits the two from the start
(`images_in` + `describe_attachments`). These tests assert the server does the same.
"""

from __future__ import annotations

import io

from PIL import Image
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.server.views import transcript


def _png(width: int = 640, height: int = 480) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (width, height), 'red').save(buffer, 'PNG')
    return buffer.getvalue()


def test_a_prompt_with_an_image_keeps_its_text_and_describes_the_picture() -> None:
    """The marker stays in the text — it is what ties a sentence to a picture — and the
    image becomes a description, never its bytes."""
    request = ModelRequest(
        parts=[UserPromptPart(content=['mira [Image #1]', BinaryContent(data=_png(), media_type='image/png')])]
    )

    item = transcript([request])[0]

    assert item['kind'] == 'user'
    assert item['text'] == 'mira [Image #1]'
    attachment = item['attachments'][0]
    assert attachment['kind'] == 'image'
    assert (attachment['width'], attachment['height']) == (640, 480)
    assert attachment['label'] == '640×480 PNG'
    assert attachment['size'].endswith('KB')
    assert 'BinaryContent' not in str(item)


def test_the_payload_never_reaches_the_projection() -> None:
    """The point of the whole exercise: no escaped bytes in the output."""
    request = ModelRequest(parts=[UserPromptPart(content=['x', BinaryContent(data=_png(), media_type='image/png')])])

    rendered = str(transcript([request]))

    assert '\\x89' not in rendered
    assert 'PNG\\r\\n' not in rendered
    assert 'data=b' not in rendered


def test_a_text_only_prompt_is_unchanged() -> None:
    """The common case must not grow a field nobody asked for."""
    item = transcript([ModelRequest(parts=[UserPromptPart(content='hola')])])[0]
    assert item['text'] == 'hola'
    assert 'attachments' not in item


def test_a_tool_that_returns_an_image_is_described() -> None:
    """`read_file` on a PNG puts a `BinaryContent` in the history."""
    request = ModelRequest(
        parts=[ToolReturnPart('read_file', BinaryContent(data=_png(), media_type='image/png'), tool_call_id='c1')]
    )

    item = transcript([request])[0]

    assert item['kind'] == 'tool_result'
    assert item['text'] == '1 image (640×480 PNG, 1.9 KB)' or item['text'].startswith('1 image (640×480 PNG')
    assert '\\x89' not in item['text']


def test_raw_bytes_in_a_tool_result_are_a_size_not_a_repr() -> None:
    """A tool that returns a `bytes` blob: the size is the only useful fact."""
    request = ModelRequest(parts=[ToolReturnPart('run', b'\x89PNG\x00\xff' * 100, tool_call_id='c2')])

    item = transcript([request])[0]

    assert item['text'] == '<600 B of binary data>'


def test_a_failed_tool_whose_reason_is_binary_is_described_too() -> None:
    """A retry is a tool that failed and is being told why; its reason can be binary."""
    request = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name='read_file',
                content=BinaryContent(data=_png(), media_type='image/png'),
                tool_call_id='c3',
                outcome='retried',
            )
        ]
    )

    item = transcript([request])[0]

    assert item['ok'] is False
    assert '\\x89' not in item['text']


def test_a_retry_is_not_painted_as_a_success() -> None:
    """`outcome` is the only thing that says so: the part kind is the same one a
    successful return uses, and the status color is what a reader scans for."""
    request = ModelRequest(parts=[ToolReturnPart('run', 'command not found', tool_call_id='c6', outcome='retried')])

    item = transcript([request])[0]

    assert item['ok'] is False
    assert item['text'] == 'command not found'


def test_normal_text_and_tool_calls_are_untouched() -> None:
    """No regression on the shapes that already worked."""
    response = ModelResponse(parts=[TextPart('hola'), ToolCallPart('run', {'cmd': 'ls'}, tool_call_id='c4')])
    items = transcript([response])

    assert items[0] == {'kind': 'assistant', 'index': 0, 'text': 'hola'}
    assert items[1]['kind'] == 'tool_call'
    assert items[1]['args'] == {'cmd': 'ls'}


def test_a_binary_argument_is_summarized() -> None:
    """Arguments can carry bytes too, and a repr of them is just as unreadable."""
    response = ModelResponse(
        parts=[ToolCallPart('write_file', {'path': 'a.png', 'data': b'\x89PNG' * 50}, tool_call_id='c5')]
    )

    item = transcript([response])[0]

    assert item['args']['path'] == 'a.png'
    assert item['args']['data'] == '<200 bytes>'
