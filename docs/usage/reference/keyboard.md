# Terminal keyboard shortcuts

Use these keys in the terminal client. Modified-key support depends on your
terminal; slash commands remain an alternative when a shortcut is unavailable.

## Compose and navigate

The composer keeps the keyboard while you read the conversation:

| Key                     | Action                                                            |
| ----------------------- | ----------------------------------------------------------------- |
| **Enter**               | Send the message, including a correction during a turn            |
| **Shift+Enter**         | Insert a line when the terminal reports modifiers                 |
| **Ctrl+P**              | Open the searchable command palette                               |
| **Ctrl+A**              | Open the agent picker                                             |
| **Ctrl+M**              | Open the model picker on terminals that distinguish it from Enter |
| **Ctrl+L**              | Open the reasoning-effort picker                                  |
| **Ctrl+V**              | Paste an image from the desktop clipboard, or text                |
| **Page Up / Page Down** | Scroll the conversation by a page                                 |
| **Ctrl+↑ / Ctrl+↓**     | Scroll the conversation by a few lines                            |
| **Alt+D**               | Toggle the debug view                                             |
| **Ctrl+T**              | Open the session's agent tree                                     |
| **Esc**, then **Esc**   | Stop a running turn if confirmed within three seconds             |

On legacy terminals, Ctrl+M and Enter can be the same input. Enter still sends
the message; use `/model` instead. See
[Terminal controls](../guides/terminal.md) for images and display behavior.

## Pick from a list

Use these keys in command and model pickers:

| Key                            | Action                                         |
| ------------------------------ | ---------------------------------------------- |
| **↑ / ↓**                      | Move the highlight                             |
| **Enter**                      | Select or run the highlighted item             |
| **Tab**                        | Complete the highlighted command or model      |
| **Esc**                        | Cancel or go back                              |
| **Ctrl+F** in the model picker | Toggle the highlighted model's favorite status |

## Answer questions and inspect subagents

Question panels and the agent tree have additional controls:

| Context    | Key                 | Action                                             |
| ---------- | ------------------- | -------------------------------------------------- |
| Question   | **Tab**             | Move between options and the custom-answer field   |
| Question   | **Alt+← / Alt+→**   | Move between questions and keep existing answers   |
| Question   | **Ctrl+↑ / Ctrl+↓** | Scroll the chat without leaving the panel          |
| Question   | **Esc**             | Cancel the question and stop the turn in one press |
| Agent tree | **Enter**           | Open the selected execution                        |
| Agent tree | **b**               | Send the selected work to the background           |
| Agent tree | **k**               | Stop the selected branch and its descendants       |
| Agent tree | **/**               | Filter the list                                    |
| Agent view | **Esc**             | Return to the parent execution                     |

Read [Subagents](../guides/subagents.md) for delegation and stopping behavior.

---

[Documentation index](../../README.md) · [User documentation](../README.md)
