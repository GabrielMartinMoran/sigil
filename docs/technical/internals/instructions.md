# Project instruction resolution

The full session history (the **log**) is append-only and is persisted as is.
What gets sent to the model on each request is a derived **view**: a
deterministic projection that does not mutate the log. That way it can be
trimmed without losing anything `/load` restores.

## Instructions per directory tree

`AGENTS.md` (or `CLAUDE.md` as a fallback) is resolved over the active directory
tree:

- The session root's `AGENTS.md` always applies.
- A subdirectory's applies only when the agent operates inside it.
- The order is parent → child: the child goes last and takes precedence.
- In the same directory, `AGENTS.md` wins over `CLAUDE.md`.

The block travels through the instructions channel (operator content), never as
a user message, and uses `on_change='append'`: when the scope widens mid-turn
the child text is appended instead of rewriting the prefix, which is what would
break the prompt cache.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
