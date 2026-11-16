# The two surfaces: `sigil server` and `sigil serve`

The backend and the web client are two commands, not one. `sigil server` is the
API alone; `sigil serve` is the same process plus `web/dist`. Only `serve` has a
password, because only `serve` has a browser to type one into, and only `serve`
checks that the committed bundle is fresh before it opens the socket.

|                              | `sigil server`       | `sigil serve`            |
| ---------------------------- | -------------------- | ------------------------ |
| `/api/*`, `/events`          | yes                  | yes                      |
| `web/dist`, `/static`        | not mounted          | mounted                  |
| `GET /`                      | a JSON notice, below | `index.html`             |
| `/api/login`, `/api/logout`  | absent: `404`        | present                  |
| Credential                   | the endpoint token   | the cookie, or the token |
| `--password`, `--rebuild-ui` | not defined          | defined                  |
| Bundle staleness at startup  | not checked          | refused when stale       |

`GET /` on the backend answers `200` with the sentence that names the command:

```json
{
  "service": "sigil backend",
  "web": false,
  "message": "This is the Sigil backend; run `sigil serve` for the web client."
}
```

A wrong URL and a right one deserve different answers: the API is not a broken
web server, so it says where the page lives instead of a `404` that says
nothing. The bundle is not read at all — `create_app` derives the surface from
`ServerConfig.web` and keeps `assets` as `None`, so a `dist` directory on disk
is never mounted by accident.

`SIGIL_SERVER_PASSWORD` and the per-run password belong to `serve`. The backend
ignores that variable rather than defaulting it away: an exported secret must
not turn an API into a process that prints one or accepts a login.
`SIGIL_SERVER_ORIGINS` and its CORS middleware stay on both surfaces, because
the client served _elsewhere_ is exactly the case that setting exists for.

The terminal's launcher starts `server` (`default_command`), so attaching a TUI
publishes no page and invents no credential. The operational consequence is
worth stating: `serve` while a backend is running fails with the ownership
message from [part A](service-runtime.md#durable-server-consumer-part-a) instead
of attaching. This cut has no proxy between the two processes; a page that needs
the API while a TUI owns the store is not supported yet. The lock is released
when the owning process ends.

The terminal launcher marks its server as client-managed. Each attached terminal
renews an in-memory presence lease and removes it on normal exit. A lease
expires 60 seconds after its last heartbeat, so an interrupted client can be
removed and register again when it reconnects. The server stops only after the
presence pool is empty, no work remains, and a short grace period passes. It
refuses new requests once shutdown begins. An explicitly started `server` or
`serve` does not use this automatic shutdown policy; its existing `--idle-grace`
option remains available.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
