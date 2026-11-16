# Models, reasoning effort, and defaults

Use `/model` to choose the model for the current conversation. Use
`/default-models` when you want a new conversation or a particular agent to
start with a repeatable default. These are different choices.

## Change the current conversation

Open `/model`, filter by account or model, and select a model. Choose its
reasoning effort when prompted. **Esc** cancels without changing the active
selection; **Ctrl+F** marks or unmarks a favorite in the model picker.

Use `/effort` to open the effort picker or `/effort high` to set a level
directly. Only the levels the selected model supports are accepted. A model
switch keeps the previous effort if supported and falls back to the model
default otherwise.

The selection is saved with the session. Loading a saved conversation restores
its selection rather than applying a new default over it. If its account was
removed, Sigil retains the usable current selection and reports why.

## Set defaults through the terminal

Run `/default-models` while idle. Choose the configuration scope and target: a
global default, a project default, or a named agent. The screen uses the
service's configuration, including when the terminal is attached to it.

Selecting a main agent applies that profile's configured model and effort. You
can override either afterward with `/model` or `/effort`. A configured subagent
uses its own selection; an unconfigured subagent inherits its caller's model and
effort, including when nested.

## Set defaults in configuration

Use `.sigil/config.json` in your project or `config.json` under `SIGIL_HOME` for
defaults shared across projects. Each choice names an existing account alias, a
supported model ID, and an effort:

```json
{
  "default_model": {
    "account": "work",
    "model": "your-model-id",
    "effort": "default"
  },
  "agents": {
    "wizard": {
      "account": "work",
      "model": "your-main-model-id",
      "effort": "high"
    },
    "scout": {
      "account": "work",
      "model": "your-research-model-id",
      "effort": "low"
    }
  }
}
```

Replace the example IDs and alias. Recognized effort names are `default`, `off`,
`minimal`, `low`, `medium`, `high`, `xhigh`, and `max`; a model needn't support
all of them. The project entry overrides the global one for the same target.
`SIGIL_CONFIG` selects one config file instead of both.

## Understand which model wins

A conversation resolves its selection in this order:

1. Its own saved model selection.
2. Its agent's configured selection in `agents.<name>`.
3. The configured `default_model`.
4. The last-used model when no stronger choice exists.

A bad default is reported rather than silently accepted. The account must exist
and its provider must accept the model and effort. See the
[configuration file map](files-and-directories.md) if a setting isn't applying.

## Read model capabilities

Catalog entries carry context-window and effort information, so the sidebar and
pickers don't need to guess. Sigil saves those capabilities with the selection
and can use cached or built-in fallback information when discovery fails.
Catalog data can still be incomplete; an arbitrary custom ID isn't a guarantee
of support, image capability, or a known context budget.

For service-side selection and projections, read
[Service commands](../../technical/architecture/service-commands.md) and
[Agent profiles](../../technical/internals/agent-profiles.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
