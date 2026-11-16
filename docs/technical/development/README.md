# Develop Sigil

Use these references to work on Sigil itself. Start with the
[contribution guide](../../CONTRIBUTING.md) for reporting issues, preparing a
change, and opening a pull request. To install Sigil for everyday use, follow
[the user installation guide](../../usage/getting-started/installation.md).

## Prepare and make a change

Follow the component boundaries and keep changes focused:

1. [Set up the development checkout](setup.md).
2. Read the relevant [architecture](../architecture/README.md) or
   [internal contract](../internals/README.md).
3. Add focused behavioral coverage, implement the change, and run the
   [required verification](testing.md).
4. Review the actual diff and update affected user and technical documentation.

## Work on a specific surface

Use the guide that owns the boundary:

- [Python tests](testing.md): component selections, isolation, and quality
  gates.
- [Web client](web-client.md): Node setup, development server, and committed
  bundle.
- [Git hooks](git-hooks.md): installation, automatic fixes, and escape hatches.
- [Local tool extensions](local-tools.md): Python API, lifecycle, permissions,
  and compaction contracts.

Measurements and live-model evaluations are separate from normal correctness
checks. Read their [performance](../performance/README.md) and
[evaluation](../evaluation/README.md) boundaries before running them.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
