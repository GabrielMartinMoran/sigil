# Troubleshooting

Start with the symptom below. Preserve your saved data and inspect the reported
error before deleting files, changing permissions, or starting another server.

## Sigil won't start

Check the prerequisite named by the error:

- **`uv` is missing:** install it from the
  [official uv instructions](https://docs.astral.sh/uv/getting-started/installation/)
  and check that it is on `PATH`.
- **Python is unavailable:** the project requires Python 3.14 or newer. Follow
  the [installation guide](../getting-started/installation.md).
- **`sigil` isn't found:** use the checkout launcher's absolute path or check
  your symlink and `~/.local/bin` entry on `PATH`.
- **A startup value is invalid:** correct the named setting using the
  [environment reference](../reference/environment.md). Don't remove your
  database.

## The agent has no model

Run `/provider` to add an account, then `/model` to choose a model for it. If
the provider rejects a key, replace it with a valid provider-issued key. Network
or plan-capacity warnings aren't the same as invalid credentials. See
[Providers and accounts](../configuration/providers.md).

If a custom model fails, check that the account supports that model ID and
reasoning effort. The model picker can accept an ID absent from its catalog;
that doesn't guarantee the provider will serve it.

## The browser shows JSON instead of a login page

You started the API-only surface. Use `sigil serve`, not `sigil server`, for the
browser. Stop the existing server normally before switching surfaces; only one
server can own the database. See the [browser guide](../guides/browser.md).

## The web bundle is missing or stale

The browser server refuses to serve a bundle that doesn't match the sources. Use
a checkout with a matching committed bundle. If you're developing Sigil, follow
the [web-client build guide](../../technical/development/web-client.md) or use
`sigil serve --rebuild-ui` with Node and the web dependencies installed. Node
isn't required to use a matching prebuilt bundle.

## A model or session change is refused

Wait for the running turn to finish, or
[stop it](../guides/conversations.md#stop-a-turn). Commands that change the
active model, agent, or tools cannot run underneath a live turn. Selecting
another session in the service-backed client is different from reconfiguring the
running session. See
[Commands during a running turn](../reference/commands.md#commands-during-a-running-turn).

## An MCP server or extension isn't available

For MCP, open `/mcp` and inspect the server error. Check the transport fields,
referenced environment variables, and configured working directory in the
[MCP guide](../configuration/mcp.md). A broken server doesn't disable all
others.

For a project Python extension, confirm that you approved trust and that its
entry point is `tools.py`. Tools must be asynchronous. See
[Local tools](../customization/local-tools.md).

## A shortcut or image paste doesn't work

Use slash commands when the terminal doesn't distinguish modified keys.
`Shift+Enter` and `Ctrl+M` depend on terminal keyboard support. Image pasting
also needs a desktop clipboard helper and a suitable model. See
[Terminal controls](../guides/terminal.md).

## The service disconnects

The terminal attempts recovery automatically. For its limits, log locations, and
a step-by-step investigation, use [Service recovery](service-recovery.md). A
restarted process cannot resume live tasks that died with the old process.

---

[Documentation index](../../README.md) · [User documentation](../README.md)
