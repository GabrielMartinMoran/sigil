# Providers and accounts

Connect your own provider account to use models in Sigil. Manage accounts with
`/provider`, then use `/model` to choose a model across those accounts. You can
have multiple accounts from the same provider.

## Choose a provider

Sigil supports these combinations:

| Provider   | Product | Sign-in                                   | Model discovery                                   |
| ---------- | ------- | ----------------------------------------- | ------------------------------------------------- |
| OpenRouter | —       | API key                                   | Public catalog                                    |
| OpenCode   | Zen     | API key                                   | Public catalog                                    |
| OpenCode   | Go      | API key                                   | Zen catalog plus custom IDs; Go publishes no list |
| OpenAI     | —       | API key or supported ChatGPT-plan sign-in | API catalog plus built-in model profiles          |

You need credentials or plan access from the provider, not an account with
Sigil. Prices, model availability, and sign-in eligibility are the provider's.

## Add an account

Complete the terminal flow while no turn is running:

1. Run `/provider` and choose to add an account.
2. Select the provider, product, and sign-in method offered by the UI.
3. Enter the requested credential or finish the browser sign-in.
4. Give the account an alias you can recognize, such as `personal` or `work`.
5. Run `/model` and choose an available model and reasoning effort.

The API key is checked before saving. A rejected key isn't written. If a network
or plan-capacity problem makes validation inconclusive, Sigil can save the
account with a warning instead. A saved account therefore doesn't prove every
model request will succeed.

If setup doesn't discover a default model, choose one explicitly with `/model`.
Use the picker to filter accounts and models. You can enter an unlisted model
ID, but your provider must support it.

## Rename or remove an account

Open `/provider` and select the existing account to rename or remove it.
Removing an account also removes its saved credential. A conversation that
referred to a removed account can't recreate that credential by loading its
history.

Account, model, and effort changes affect subsequent turns without a restart.
They are refused while a turn is running.

## Know where credentials are stored

By default, Sigil separates account metadata from secrets:

```text
~/.sigil/sigil.db                      # aliases, provider metadata, app state
~/.sigil/providers/credentials.json    # API keys and OAuth tokens; mode 0600
```

The credential file isn't an OS keyring. Software running as your user can
potentially read it; protect your home directory and backups. Provider keys
don't belong in project configuration or source control.

`SIGIL_HOME` changes the data home. See
[Files and directories](files-and-directories.md) before moving state.

## Next steps

Set [model defaults](models.md) or return to
[your first conversation](../getting-started/first-conversation.md). For how
accounts and selections are persisted, read the
[technical storage reference](../../technical/architecture/storage.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
