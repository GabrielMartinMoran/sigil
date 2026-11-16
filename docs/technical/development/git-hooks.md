# The pre-commit hook

Hooks are tracked in `.githooks/`, and `core.hooksPath` — which is per-clone
configuration that git cannot ship — is set automatically by whichever happens
first:

| Entry point                              | When it fires                                   |
| ---------------------------------------- | ----------------------------------------------- |
| `./sigil`                                | on launch, for a clone that has never run Sigil |
| `npm ci` / `npm install` in `web/`       | through the `prepare` script                    |
| `uv run python scripts/install_hooks.py` | by hand, with the full report                   |

All three are idempotent and silent when there is nothing to do.
`SIGIL_NO_HOOKS=1` opts out. A clone that is never launched, never gets
`npm ci`, and never runs the installer still has no hooks — the checkout is the
only thing git can share, and the gate lives here because a hook in `.git/hooks`
would not travel at all.

On every commit, `scripts/pre_commit.py` does four things, in this order:

1. `ruff format` and `ruff check --fix` on the staged Python files, then
   re-stages what it rewrote.
2. `prettier --write` and `eslint --fix` on the staged files under `web/`.
3. Rebuilds `web/dist` when anything under `web/` (other than the bundle itself)
   changed, and stages the result — so a commit that touches the client carries
   the bundle that commit produces.
4. Refuses the commit when a rule cannot be fixed automatically, naming the
   file, the rule and the way out.

| Variable            | Effect                                                     |
| ------------------- | ---------------------------------------------------------- |
| `SKIP_SIGIL_HOOK=1` | skips the whole gate (rebase, WIP commit)                  |
| `SKIP_PY_LINT=1`    | skips ruff                                                 |
| `SKIP_WEB_LINT=1`   | skips prettier and eslint                                  |
| `SKIP_WEB_BUILD=1`  | skips the rebuild — the commit will carry a stale bundle   |
| `SIGIL_NO_HOOKS=1`  | leaves `core.hooksPath` alone: the hooks are never enabled |

`scripts/check_hook.sh` exercises the gate end to end in a throwaway clone (real
git, real ruff, real npm, real rebuild), because a hook that stops working fails
silently: nobody notices until the committed bundle is wrong.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
