# Technical documentation

Use this path to develop Sigil, write tool extensions, or investigate its
implementation. To configure or operate it, use the separate
[user documentation](../usage/README.md).

## Start with the boundary you need

Choose a route based on the change or investigation:

- [Contributing](../CONTRIBUTING.md): report issues and prepare a pull request.
- [Development](development/README.md): setup, tests, web builds, and Git hooks.
- [Architecture](architecture/README.md): domain, storage, service lifecycle,
  and client boundaries.
- [Internals](internals/README.md): context projection, tools, profiles, agent
  execution, rollback, and terminal behavior.
- [Performance](performance/README.md): measured results and reproduction.
- [Evaluation](evaluation/README.md): offline smoke checks and opt-in live
  comparisons.
- [Design history](design/README.md): preserved proposals and original
  decisions.

## Read evidence in context

Implementation records include rationale and the measurements taken when work
was done. A reported test count or latency is evidence for that checkout, not a
promise about the current one. Earlier proposals are labeled separately from
current user-facing behavior. Check source and tests before changing a contract.

---

[Documentation index](../README.md)
