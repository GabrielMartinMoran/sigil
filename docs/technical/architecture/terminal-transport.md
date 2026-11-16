# The terminal's transport (part F)

The terminal still executes in process. This part adds only the client it will
use once it stops: `src/ui/client/service_client.py`, one `ServiceClient` per
attached service. The widgets, the five mixins and the removal of the in-process
fallback are `tui-attach-v1`; nothing here imports Textual or touches a screen.

`ServiceClient.connect` asks `ensure_service` in
[part C](service-launcher.md#the-launcher-part-c) for a live service — attaching
when one answers, launching when none does — and wraps the endpoint it gets.
Every other method is one route, one typed value and one typed failure:

| Method                   | Route                      | Replies                                    |
| ------------------------ | -------------------------- | ------------------------------------------ |
| `state()`                | `GET /api/state`           | the projection the screen paints from      |
| `durable()`              | `GET /api/session/durable` | transcript, queue, deliveries, recoveries  |
| `send_turn(TurnRequest)` | `POST /api/turn`           | `accepted`, `request_id`, stored image ids |
| `abort()`                | `POST /api/abort`          | whether a turn was stopped                 |
| `answer(id, answers)`    | `POST /api/answer`         | whether the question was still pending     |
| `events(after=…)`        | `GET /events`              | an async iterator of `ServiceEvent`        |

## Why `httpx` and not `urllib` in a thread

The terminal is one asyncio loop: an event stream, a render loop and a turn
share it. `urllib` and `http.client` are blocking, so every call would need
`asyncio.to_thread`, and the stream — the one request that stays open for
minutes — cannot go to a thread without a queue in front of it and a way to stop
reading. `httpx.AsyncClient` provides an awaitable request, a cancellable read
and a streaming body natively, and the parity tests already drive the real app
through its ASGI transport. It is therefore declared in `pyproject.toml`:
production code now imports it, even though it arrived transitively before.

`ensure_service` is the one call that stays blocking, because it may spawn a
process and watch for twenty seconds. `connect` runs it with
`asyncio.to_thread`, so a service that takes its time to start does not freeze
the screen.

## The transport is a constructor argument

`ServiceClient(endpoint, transport=…)` takes the HTTP transport, and that is
what makes this module testable without a socket or a child process. Two double
exist for two different jobs:

- **`httpx.ASGITransport` against `create_app`** proves the wiring: the bearer
  token is accepted, the state and the durable read come back as the projections
  `views` defines, a turn carries its images in order, and each refusal arrives
  as its named failure.
- **A scripted transport** covers what the ASGI transport cannot. It buffers a
  response and asserts it completed, so a stream that never ends cannot pass
  through it. The scripted one hands over bytes a real server would produce only
  by failing: an event cut before its blank line, a body that quotes the token.

## The token is a header, and it is scrubbed

The token is read from the endpoint file, put once into the client's default
headers, and never written anywhere else. It is not in the base URL, so it
cannot reach a proxy log through a request line. It is not in a message and not
in `repr`: a client is printed in a debug report, and a report must not carry a
credential. As a second barrier, every message the service sends is passed
through the token before it becomes an exception — a service that echoes the
credential in an error body becomes a redacted line in the terminal, not a leak.

## The SSE parser is a state machine

`SseDecoder` reads bytes and returns frames; it knows nothing about HTTP. A read
can end in the middle of a field, of a line, of an event, or of a multi-byte
character, so it accumulates, consumes whole lines and dispatches only on the
blank line. It joins several `data:` lines with `\n`, ignores comment lines —
including the service's `: keep-alive` heartbeat — and reads `\n`, `\r\n` and
`\r`. An event cut before its blank line is held and never emitted: an
incomplete frame is a broken event, not a short one.

## One connection per stream, and the cursor belongs to the caller

`events(after=…)` opens one connection and sends the cursor as both
`Last-Event-ID` and `?after=`, because a browser resends the header by itself
while a hand-rolled client passes the query, and the route reads either. Closing
the iterator is a clean cut: the connection is released and nothing is raised.

A stream the service ends on its own is `StreamInterrupted`, carrying
`last_event_id`. Reconnecting is `events(after=that id)`, and the service
replays what was missed — the reason every event carries a sequence number. The
client does not reconnect by itself: that needs a delay between attempts, and a
retry policy belongs to the screen that can also tell the person what is
happening.

## The typed failures

The screen can distinguish a retryable connection loss from a request the
service refused.

| Failure              | `code`                | When                                        | What the screen does                                           |
| -------------------- | --------------------- | ------------------------------------------- | -------------------------------------------------------------- |
| `ServiceUnavailable` | `service_unavailable` | nothing answered, or the launcher gave up   | retry; `launch_code` says `service_starting` or `service_died` |
| `Unauthorized`       | `unauthorized`        | `401`: the token is not this run's          | attach again; this run is gone                                 |
| `ServiceBusy`        | `busy`                | `409`: the session is in the way            | wait for the turn, then repeat                                 |
| `RequestRejected`    | `rejected`            | `400`: the request names something unusable | fix the message                                                |
| `PayloadTooLarge`    | `too_large`           | `413`: the body passed the command ceiling  | fewer or smaller images                                        |
| `UnexpectedResponse` | `unexpected_response` | a status or body this client cannot name    | report it; the service and the client disagree                 |
| `StreamInterrupted`  | `stream_interrupted`  | the stream ended without being closed       | `events(after=last_event_id)`                                  |

No `httpx` exception reaches the caller, and no failure is swallowed: a status
the client does not know is `UnexpectedResponse` with the status in the message,
not a silent `None`.

## What is not decided here

The transport boundary does not own these application or presentation decisions.

- **The widgets.** Streaming deltas into the transcript, questions, images and
  rollback stay where they are; `tui-attach-v1` replaces their source, not their
  behaviour.
- **Retrying.** `TurnRequest` generates the `request_id` on the client and keeps
  it, so retrying the same instance is the correct retry and a new instance is a
  new message. How many times to retry, and when, is the caller's.

The question deadline used to be on this list. It is not any more: a question
now has a bound and a rule for what the user sees when it passes, documented
under [the question deadline](terminal-client.md#the-question-deadline).

The tests live in `tests/tui/test_ui_service_client.py`: unit tests for the
protocol, ASGI contract tests for the routes, and scripted-transport tests for
the stream. None of them sleeps, and none needs a port.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
