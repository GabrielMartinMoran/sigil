"""Agent Skills protocol support for Sigil.

A skill is a directory with a ``SKILL.md`` (YAML frontmatter + Markdown
body) and optional resources (``scripts/``, ``references/``, ``assets/``).

The module implements the full consumption protocol:

- discovery in ``<cwd>/.agents/skills`` and ``~/.agents/skills``,
- *progressive disclosure*: metadata always available, body on demand,
  resources and scripts when they are needed,
- safe resource reading (no directory escapes),
- execution of scripts bundled inside the skill.

The package re-exports no symbols: import from the owning module.
"""
