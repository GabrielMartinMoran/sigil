# Give Sigil project instructions

Put repository-specific working rules in `AGENTS.md`. Sigil resolves applicable
instructions from the active directory tree, so the rules can follow the part of
the project the agent is working on.

## Add rules at the right scope

Use the workspace root for project-wide guidance. Add a deeper `AGENTS.md` when
a subdirectory needs different commands or conventions:

```text
my-project/
├── AGENTS.md
├── backend/
│   └── AGENTS.md
└── frontend/
    └── AGENTS.md
```

The root's instructions always apply. A subdirectory's instructions apply when
the agent operates there. Parent content comes before child content; the more
specific child rules take precedence. If both `AGENTS.md` and `CLAUDE.md` exist
in one directory, Sigil uses `AGENTS.md`; `CLAUDE.md` is the fallback.

## Write actionable guidance

Include the commands, boundaries, and project conventions needed to work safely.
For example, name the test command for that component and explain which
generated files aren't edited by hand. Keep credentials and secrets out of
instruction files.

Project instructions guide model behavior; they don't create an OS sandbox. Use
[agent permissions](agents.md#permissions) for tool-access rules and read
[Safety](../guides/safety.md) for their limits.

For prompt scope and cache behavior, read the
[instruction-resolution contract](../../technical/internals/instructions.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
