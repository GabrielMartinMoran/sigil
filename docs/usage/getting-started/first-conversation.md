# Start your first conversation

Connect a provider, pick a model, and ask Sigil to investigate a small task.
This guide assumes the [terminal is already open](installation.md) in the
project you want to work on.

## 1. Connect your provider

Type `/provider` and press **Enter**. Add an account, choose its provider and
available product or sign-in method, and follow the prompts. Give the account a
recognizable alias, such as `personal` or `work`.

Sigil supports OpenRouter, OpenCode, and OpenAI accounts. Available sign-in
methods depend on the provider; you don't need to export a provider key in your
shell or commit one to a project file. Credentials are stored separately from
conversation data. See [Providers and accounts](../configuration/providers.md)
for validation, storage, and sign-in details.

## 2. Choose a model

Type `/model`, filter the list, and select a model from your account. Choose a
reasoning effort when the picker offers it. The sidebar shows the selection that
the next turn will use.

If your account was added but no default model was found, selecting one here
completes setup. A custom model ID must still be supported by your provider. Use
`/effort` later to change a supported reasoning level.

## 3. Ask for a small investigation

Send a concrete request that is easy to inspect, for example:

```text
Explain how this project starts. Identify the entry point and configuration
files. Don't change any files.
```

You can follow tool activity in the chat and the plan in the sidebar. The
built-in Wizard investigates and asks for approval before implementation; that
is its instructed workflow, not a runtime security lock. Read
[Permissions and safety](../guides/safety.md) before granting broad tool access.

When Sigil asks a question, choose an option with the arrow keys and press
**Enter**, or type a custom answer. **Esc** cancels the question and stops the
turn without choosing for you.

## 4. Continue or leave

Ask a follow-up in the same conversation. To start a separate one, use `/new`.
To return to a saved one, use `/load`. Use `/quit` to close the terminal. In the
default service-backed mode this detaches the client; it doesn't stop work still
running in the service.
[Stop the turn explicitly](../guides/conversations.md#stop-a-turn) first if you
want it cancelled.

Your session keeps its conversation, model selection, and agent. You don't need
to export the chat before closing.

## Next steps

Learn the [terminal controls](../guides/terminal.md),
[set model defaults](../configuration/models.md), or
[open Sigil in the browser](../guides/browser.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
