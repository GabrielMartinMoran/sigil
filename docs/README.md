# Sigil documentation

Use Sigil without learning its internals. Start with installation, connect your
provider, and follow the guides for the task you want to complete.

## Start using Sigil

New here? Follow the [getting-started path](usage/getting-started/README.md):
install Sigil, choose a model, and send your first message.

| I want to…                             | Open this guide                                                        |
| -------------------------------------- | ---------------------------------------------------------------------- |
| Install and launch Sigil               | [Installation](usage/getting-started/installation.md)                  |
| Connect an account and choose a model  | [Your first conversation](usage/getting-started/first-conversation.md) |
| Configure providers, models, or MCP    | [Configuration](usage/configuration/README.md)                         |
| Learn the terminal controls            | [Terminal guide](usage/guides/terminal.md)                             |
| Use the browser instead                | [Browser guide](usage/guides/browser.md)                               |
| Resume work or use a separate worktree | [Sessions and workspaces](usage/guides/sessions.md)                    |
| Undo a turn or branch a conversation   | [Rollback and forks](usage/guides/rollback.md)                         |
| Add agents, skills, tools, or themes   | [Customization](usage/customization/README.md)                         |
| Look up a command, key, or setting     | [Reference](usage/reference/README.md)                                 |
| Fix a problem                          | [Troubleshooting](usage/troubleshooting/README.md)                     |

Before granting tools more access, read
[permissions and safety](usage/guides/safety.md). Provider requests and
connected tools can use external services; local storage doesn't mean every
operation stays on your machine.

## Develop or understand Sigil

The [technical documentation](technical/README.md) is a separate path for
contributors and extension authors. You don't need it to configure or use Sigil.

- [Contributing](CONTRIBUTING.md): bug reports, focused changes, and pull
  requests.
- [Development](technical/development/README.md): environment, tests, web
  client, and Git hooks.
- [Architecture](technical/architecture/README.md): vocabulary, storage, service
  boundaries, and client transports.
- [Internals](technical/internals/README.md): context, tools, agent execution,
  and rollback contracts.
- [Performance and evaluation](technical/performance/README.md): measurements,
  reproduction steps, limitations, and tool-use evaluation.
- [Design history](technical/design/README.md): earlier proposals and the
  decisions they explain, not current setup instructions.

## How these guides fit together

Usage pages explain what to do and what to expect. Reference pages collect exact
commands and settings. Technical pages explain how the implementation works and
link back to the relevant user guide. Historical records are labeled so an old
measurement or target design isn't mistaken for a current guarantee.

If you only want to use Sigil, continue with the [usage guide](usage/README.md).
To return to the project overview, open the [repository README](../README.md).
