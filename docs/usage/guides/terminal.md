# Use the terminal interface

The composer is where you send messages and commands. The chat shows the work;
the sidebar shows the active model, context, cost, loaded skills, integrations,
and plan. You can hide the sidebar without losing its state.

## Send messages and find commands

Type a message and press **Enter**. **Shift+Enter** adds a line on terminals
that report key modifiers. On older terminals it may act as Enter instead, so
check terminal support before relying on it for a multiline draft.

Type `/` to filter slash commands, or use **Ctrl+P** for the searchable command
palette. Use the arrow keys to choose, **Enter** to run, and **Esc** to cancel.
**Tab** completes a highlighted command or model. `/help` lists commands and
keys.

You can scroll with the mouse wheel, **Page Up / Page Down**, or **Ctrl+↑ /
Ctrl+↓** without moving the keyboard away from the composer. A large pasted
message scrolls inside the composer rather than taking over the screen.

## Understand the status and sidebar

The status line identifies the active agent, current activity, workspace and
branch, and Git change counts. A spinner means work is in progress; **Ready**
means the agent is waiting for you. New untracked files are counted as files,
not invented line changes.

The sidebar shows the model and account, supported effort, projected context,
and session cost. The roadmap belongs to the agent currently on screen. Open a
subagent to see its own plan and context. Use `/sidebar off` or `/sidebar on` to
change visibility.

## Read reasoning and tool output

Reasoning and successful tool output start collapsed. The summary lets you see
what happened without opening every result:

```text
/reasoning-visibility on
/tools expanded
```

Use `off` and `collapsed` respectively to restore the compact view. Failures
remain visible. A tool returning successfully doesn't imply the command it ran
passed: a completed shell call can report failing tests.

These display preferences persist between sessions. For numeric diagnostics, use
`/context` or `/debug --text`; ordinary use doesn't require opening debug.

## Answer a question

A question replaces the composer until you answer or cancel. The chat remains
readable above it, and your unfinished draft is preserved.

Use **↑ / ↓** and **Enter** to choose an option. **Tab** moves to the
custom-answer field; **Alt+← / Alt+→** moves between questions without
discarding answers. **Ctrl+↑ / Ctrl+↓** still scrolls the conversation. **Esc**
cancels the question and stops the turn in one press.

## Paste an image

Copy an image to the desktop clipboard and press **Ctrl+V**. Sigil inserts an
`[Image #1]` marker at the cursor. Refer to that marker in your message;
removing it with Backspace removes the image and renumbers the others.

Image paste needs a model that accepts images and a desktop clipboard helper:

| Desktop                   | Helper                                                     |
| ------------------------- | ---------------------------------------------------------- |
| X11                       | `xclip`                                                    |
| Wayland                   | `wl-paste` from `wl-clipboard`                             |
| macOS                     | `pngpaste`                                                 |
| Windows clipboard backend | PowerShell; this doesn't establish native launcher support |

Install the helper with your platform's package manager if needed. If it isn't
available, Sigil reports that and can still paste text. A known text-only model
refuses the image before sending a turn; choose another model with `/model`.

Images are resized and bounded before sending. Older images may leave the active
model context while staying saved with the session. Refer to the marker again
when you need the agent to revisit one. See
[Context and cost](context-and-cost.md).

## Next steps

Learn to [steer or stop a turn](conversations.md),
[manage sessions](sessions.md), or use the
[keyboard reference](../reference/keyboard.md). Implementation details live in
the
[terminal presentation contract](../../technical/internals/terminal-interface.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
