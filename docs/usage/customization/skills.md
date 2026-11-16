# Add Agent Skills

A skill is a reusable set of instructions in a directory containing `SKILL.md`.
It can also include references, assets, and executable scripts. Sigil supports
the [Agent Skills format](https://github.com/anthropics/skills).

## Install a skill

Put the skill directory in one of these roots:

```text
my-project/.agents/skills/my-skill/SKILL.md
~/.agents/skills/my-skill/SKILL.md
```

The project root is checked first; a project skill shadows a global skill with
the same name. `SIGIL_SKILLS_DIRS` supplies an explicit colon-separated list of
roots instead. Global skills don't live under `SIGIL_HOME` by default.

Inspect scripts before installing an unfamiliar skill. Reading a skill and
running its scripts are different capabilities; scripts can have real local or
external effects and aren't made safe by being bundled with instructions.

## Create a small skill

Add a `SKILL.md` with frontmatter and instructions:

```markdown
---
name: release-checklist
description: Use when preparing a release and checking its verification steps.
license: MIT
---

# Release checklist

Identify the release scope. Check the documented verification commands and
report any failures or unexecuted checks before recommending a release.
```

`name` must be kebab-case, at most 64 characters. Give the description a clear
activation condition; the recommended maximum is 1,024 characters. Optional
format fields include `allowed-tools`, `metadata`, `compatibility`, and
`disable-model-invocation`. A format declaration doesn't bypass the active
profile's permissions.

Use descriptive resource filenames, for example `references/release-policy.md`,
and link them from the instructions when the agent needs deeper detail.

## Understand what gets loaded

Skill discovery exposes metadata before loading full instructions:

| Stage                             | Available content                                     |
| --------------------------------- | ----------------------------------------------------- |
| Discovery                         | Skill name and description                            |
| `load_skill`                      | Full `SKILL.md` instructions and a resource inventory |
| Resource read or script execution | The requested supporting content or execution result  |

The skill body travels in the loaded-skills instruction block. Resources are
read on demand rather than all being inserted at discovery. Loading is local to
the agent execution: a subagent doesn't automatically inherit its parent's
loaded skills.

The model can use `list_skills`, `load_skill`, `read_skill_resource`, and
`run_skill_script` when its permissions allow them. Resource paths stay within
the skill directory. Script interpreters depend on file type: Sigil's Python for
`.py`, Bash for `.sh`/`.bash`, Node for `.js`, and the shebang or executable
permissions for other files. Scripts inherit the self-launch guard.

To check use, inspect **Loaded skills** in the sidebar for the agent on screen.
A clear description helps selection; it doesn't prove every model will load or
follow the skill whenever you intended.

For instruction placement and resource handling, read
[Context projection](../../technical/internals/context.md). For repository-wide
rules instead of a reusable skill, use
[Project instructions](project-instructions.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
