# Use Sigil in the browser

Start the browser surface when you want a web interface. It uses the same local
service and saved data as the terminal, but its controls aren't identical to
terminal slash commands.

## Before you start

You need an installed checkout with a matching committed `web/dist` bundle. Node
isn't required to serve that bundle. Only one server can own the database at a
time: close terminal clients and let their automatically managed backend finish,
or stop an explicitly started server normally, before switching surfaces.

## Open the web client

Run this command from the project you want to open:

```sh
sigil serve
```

If you haven't added the launcher to `PATH`, use `/path/to/sigil/sigil serve`.
The server prints its address and a generated password. Open that address
(normally `http://127.0.0.1:8760`) and sign in with the printed password.

You can set `SIGIL_SERVER_PASSWORD` before launch to use your own password.
Treat it as a secret. Keep the default loopback binding for local use; a
password gate alone isn't a reason to expose the server to the internet. Binding
beyond loopback requires your own network and transport-security arrangements.

## Work in the browser

Use the composer to send messages and the question panel to answer the agent.
Use the session controls to create or open conversations and the profile picker
in the header to choose an installed agent. Agent selection is saved with the
session and affects subsequent turns.

You can prepare [provider accounts](../configuration/providers.md) and
[model defaults](../configuration/models.md) in the terminal before opening the
browser. Browser controls aren't one-to-one equivalents of terminal slash
commands. For desktop alerts, use the header's **Enable alerts** control and see
[Notifications](notifications.md).

## Know which server you started

These launch modes serve different purposes:

| Command        | Surface                                                                      |
| -------------- | ---------------------------------------------------------------------------- |
| `sigil`        | Terminal client; finds or starts its API backend                             |
| `sigil server` | API only, authenticated with the local endpoint token; no browser login page |
| `sigil serve`  | API plus browser client and password login                                   |

`sigil server` doesn't invent a browser password. Opening it in a browser gives
an explanation pointing to `sigil serve`, not a web app.

Explicit `server` and `serve` processes stay running by default. Stop one with
**Ctrl+C** in its server terminal. `--idle-grace SECONDS` optionally enables
work-based idle shutdown. To see available flags without starting a server, run:

```sh
sigil serve --help
```

## If the page won't start

A lock error means another server owns the database; don't delete the database
or lock file to force a second owner. A stale-bundle error means the built page
doesn't match its sources. Follow
[Troubleshooting](../troubleshooting/README.md) or the
[developer web-build guide](../../technical/development/web-client.md).

For service boundaries and authentication details, read
[Service surfaces](../../technical/architecture/service-surfaces.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
