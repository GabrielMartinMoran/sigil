# Contribute to Sigil

Help improve Sigil through bug reports, documentation, tests, or code. Small,
focused contributions are welcome; you don't need to understand the whole
architecture to fix a typo or explain a confusing workflow.

## Choose your starting point

Use the path that fits what you want to contribute:

| Your goal                           | Start here                                                          |
| ----------------------------------- | ------------------------------------------------------------------- |
| Report a bug or suggest a feature   | [Open an issue](https://github.com/GabrielMartinMoran/sigil/issues) |
| Improve a guide                     | [Documentation index](README.md)                                    |
| Change Python or terminal behavior  | [Development setup](technical/development/setup.md)                 |
| Change the browser client           | [Web development](technical/development/web-client.md)              |
| Add tools without changing the core | [Local tool extensions](technical/development/local-tools.md)       |

For larger changes to behavior, architecture, or dependencies, discuss the scope
in an issue before investing in the implementation. Small corrections can go
straight to a pull request.

## Report a problem

Search existing issues first. If the problem hasn't been reported, include the
information someone else needs to reproduce it:

- What you expected and what happened instead.
- The smallest steps or example that reproduces the problem.
- The Sigil commit, operating system, and affected client: terminal or browser.
- Relevant errors or screenshots. For terminal input or rendering problems,
  include the terminal application and version.

Redact provider keys, OAuth tokens, passwords, service endpoint tokens, and
private project content. Don't upload your data directory or credential files. A
useful feature request explains the task you can't complete and why existing
workflows don't meet that need.

## Prepare a change

Keep the contribution easy to understand and review:

1. Fork the repository if you need your own writable copy, then create a branch
   for the change.
2. Follow the [development setup](technical/development/setup.md) and the
   repository's [working instructions](../AGENTS.md). For documentation-only
   work, you don't need to launch Sigil or configure a model account.
3. For code changes, read the relevant
   [architecture](technical/architecture/README.md) or
   [internal contract](technical/internals/README.md), rather than every page.
4. Add a focused regression test when changing testable behavior, then implement
   the smallest coherent fix. Keep unrelated cleanup out of the pull request.
5. Update the user guides and technical references affected by the change.

Prefer the cheapest test that proves the behavior. Use deterministic model
responses, isolated state, and bounded completion waits. Don't require live
provider credentials for ordinary tests. The
[testing guide](technical/development/testing.md) defines component boundaries
and isolation rules.

## Verify the affected components

Run checks against the final tree, not an earlier version of the change:

| Change             | Verification                                                                                                                               |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Python code        | Start with focused tests; run the complete Python suite and Ruff checks once the tree is stable                                            |
| Browser client     | Run the web client's tests, type checks, lint, formatting, and build; include relevant Python integration checks when the boundary changes |
| Documentation only | Check factual accuracy, local links, heading anchors, formatting, and the actual diff                                                      |

Follow the exact commands in the
[testing guide](technical/development/testing.md). Use eight pytest workers for
a complete Python suite or entire component, but no workers for a small focused
selection. Don't run multiple eight-worker suites simultaneously.

The [Git hooks](technical/development/git-hooks.md) can rewrite and re-stage
files. Review those changes before committing. Web-source changes also need the
matching committed `web/dist` bundle; the
[web guide](technical/development/web-client.md) explains how to build and check
it. Hooks don't replace tests or a final diff review.

## Keep documentation easy to use

Write documentation and filenames in English. Keep the repository README as a
product overview, with detailed instructions under `docs/`:

- Put installation, configuration, and everyday tasks in `docs/usage/`.
- Put implementation details and contributor references in `docs/technical/`.
- Link a new page from the relevant index so readers can find it.
- Lead with the outcome, make prerequisites explicit, and show what to do if a
  step fails. Keep technical background separate from the steps users need.
- Update links and anchors when moving a page or changing a heading. Label
  historical designs and measurements so they aren't mistaken for current
  instructions.

## Open a pull request

Give reviewers a clear explanation of the problem and the proposed solution.
Include these details in the description:

- **Purpose:** What changes and why it matters.
- **Scope:** Related issue, affected components, and anything intentionally left
  out.
- **Verification:** Commands run and their results, including failures or checks
  you couldn't complete.
- **User impact:** Configuration, compatibility, or documentation changes. Add
  before-and-after screenshots for visible interface changes when useful.

Review the diff for unrelated files, generated artifacts, and secrets before
submitting. Report performance measurements with their method and limitations;
don't present a single timing sample as a proven improvement.

## Next steps

Start with the [development index](technical/development/README.md), or return
to the [documentation index](README.md) to choose another path.
