# Agent profile prerequisite verification

The profile fix and TUI/web selection are implemented. The parent multi-session
feature remains active; none of its six concurrency slices is declared complete.
Implementation and review were performed directly under the Wizard profile,
without delegating implementation or independent QA.

## Delivered behavior

Binding a stored session restores its selectable profile before model and tree
resolution. Missing or subagent-only saved choices fall back to the selectable
`wizard`, then another selectable profile, then no profile, using the registry's
existing constructor policy. The replacement is saved. Profile selection on a
stored session is also persisted before another turn.

The server includes `ToolPermissions.describe()` in its agent projection. The
attached TUI uses these display rows, including server-only profiles, instead of
reading local profile files. Its current indicator uses the effective service
profile. The local TUI also uses deterministic restore fallback.

The web header shows the effective profile and offers selectable root agents.
Options show name, description, and origin, not permission text. Busy or pending
selection is disabled, refusals retain the old choice, and success refreshes
server state. Existing service events refresh other connected clients. No new
dependency was added; `web/dist` was rebuilt.

## Regression evidence

The following new checks failed before their implementations:

- Composition binding expected `reviewer` but retained `wizard`.
- The server projection lacked `permissions`, and the TUI helper attempted to
  use a local registry rather than projected rows.
- Web selection tests failed because `selectAgent` wasn't implemented.
- Web presentation tests couldn't import the missing presentation helper.
- A stored session still named `wizard` after selecting `reviewer`.

The final tests additionally exercise denied and permitted real `patch_file`
calls after binding, persisted invalid-profile fallback, and global/project
profile discovery across workspace changes.

The first complete Python run found an obsolete attached-TUI test expecting
Ctrl+A to refuse opening the agent picker. It was replaced with real keyboard
and command coverage that selects an offered profile. The existing busy-key
refusal tests remain. No unrelated failure was suppressed.

## Final checks

The final stable code and rebuilt web bundle passed:

| Check | Result |
| --- | --- |
| `uv run pytest -q -p no:sugar -n 8` | 2,760 passed, 1 skipped, 1 deprecation warning; 55.85 seconds reported by pytest |
| `uv run ruff check src tests` | Passed |
| `uv run ruff format --check src tests` | Passed; 529 files |
| `cd web && npm test` | 61 passed |
| `cd web && npm run typecheck` | Passed; also repeated by build |
| `cd web && npm run lint` | Passed |
| `cd web && npm run format:check` | Passed |
| `cd web && npm run build` | Passed; committed bundle regenerated |
| `git diff --check` | Passed |
| Active plan YAML parsing | All three YAML files parsed |
| New documentation relative links | Targets exist |

The earlier focused group passed 261 tests. Two subsequent assertions/tests
(global discovery and projection contract) are covered by the final complete
run; that earlier count isn't represented as a later-tree result.

## Browser review

Chrome headless exercised the built bundle against a temporary local API stub,
not the production backend. It verified 1,280 px and 320 px viewports, selectable
server profiles, exclusion of subagents and permission text, actual HTTP choice
submission, refreshed header text, picker closing, busy selection disabling,
keyboard opening, Escape closing, and restored focus. A mobile menu-positioning
defect discovered during review was fixed and the browser checks repeated.
The stub initially had incorrect routing and the first CDP attempt selected an
extension target; those harness problems were corrected before browser results
were recorded. Backend route and multi-terminal behavior were separately
covered by the Python suite.

## Additional validator findings

The experimental `clean-python/scripts/validate.sh` ran and returned exit 1.
It reports repository-wide class/module naming, multiple public classes,
class/function mixing, and missing annotations. Its class layout rules also
flag the new display-row class in the existing picker module. These findings
were not suppressed, and the experimental validator is not reported as passing.
The change preserves the established colocated picker data-class convention
instead of restructuring unrelated modules to satisfy this additional pilot.
No configured Python type-checker command was found in `pyproject.toml`.

## Self-review and remaining work

Self-review: **8/10 — Mostly Clean**. Registry fallback is shared with startup;
remote display data doesn't fabricate filesystem paths; web changes reuse the
existing tokens, Node harness, API, and state-change mechanism. This is not an
independent review. Browser rendering was checked with a stubbed API; no claim
of a production browser end-to-end run is made.

Pre-existing changes in the validator, context-window handling, summoner,
server CLI, and related tests were preserved, including the existing changes
inside `bootstrap.py`. No commit, push, deployment, or live service restart was
performed. The full suite validates the combined current tree, not ownership of
those pre-existing edits.

Remaining: all six parent multi-session slices (event filtering, execution
workspace context, runtime extraction, concurrent sessions, client scoping,
and documentation). This prerequisite alone doesn't provide concurrent sessions
or per-client session isolation.
