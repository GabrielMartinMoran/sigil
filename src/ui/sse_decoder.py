"""Decodes server-sent events into frames."""

from __future__ import annotations

import codecs

from src.ui.sse_frame import SseFrame


class SseDecoder:
    """Reads server-sent events one chunk at a time.

    A chunk boundary is not an event boundary: a read can end in the middle of a
    field, of a line, of an event, or of a multi-byte character, and a decoder that
    assumed otherwise would lose or mangle an event exactly when the network was
    busy. So bytes are accumulated, whole lines are consumed, and a frame is
    returned only when its blank line arrives. An event cut before that line is
    held and never emitted: an incomplete frame is a broken event, not a short one.

    The parser follows the protocol for the fields this service sends (`id`,
    `event`, `data`) and ignores the rest, including comment lines — the service's
    `: keep-alive` heartbeat is a comment, and it must not become an event. The
    last id is deliberately *not* carried into a frame that has none: the cursor
    here is per event, and inventing one would make a reconnect skip an event.
    """

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self._buffer = ''
        self._id: str | None = None
        self._event: str | None = None
        self._data: list[str] = []

    def feed(self, chunk: bytes) -> list[SseFrame]:
        """Every frame the chunk completed, in order. Possibly none."""
        self._buffer += self._decoder.decode(chunk)
        frames: list[SseFrame] = []
        while (line := self._take_line()) is not None:
            frame = self._consume(line)
            if frame is not None:
                frames.append(frame)
        return frames

    def _take_line(self) -> str | None:
        """The next whole line, or ``None`` while the rest is still incomplete.

        A trailing ``\\r`` may be the first half of ``\\r\\n``, so it waits for the
        next read instead of being read as a line and an empty line.
        """
        for index, character in enumerate(self._buffer):
            if character == '\n':
                line, self._buffer = self._buffer[:index], self._buffer[index + 1 :]
                return line.removesuffix('\r')
            if character == '\r':
                if index + 1 == len(self._buffer):
                    return None
                step = 2 if self._buffer[index + 1] == '\n' else 1
                line, self._buffer = self._buffer[:index], self._buffer[index + step :]
                return line
        return None

    def _consume(self, line: str) -> SseFrame | None:
        if not line:
            return self._dispatch()
        if line.startswith(':'):
            return None
        name, _, value = line.partition(':')
        value = value.removeprefix(' ')
        if name == 'id':
            self._id = value
        elif name == 'event':
            self._event = value
        elif name == 'data':
            self._data.append(value)
        return None

    def _dispatch(self) -> SseFrame | None:
        if not self._data:
            self._id = self._event = None
            return None
        frame = SseFrame(id=self._id, event=self._event, data='\n'.join(self._data))
        self._id = self._event = None
        self._data = []
        return frame
