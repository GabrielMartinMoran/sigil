# Understand context and cost

The sidebar shows how much of the model's context window the conversation is
projected to use and how much the session has cost so far. Use `/context` for a
breakdown. These numbers help you decide what to do; they aren't a spending cap.

## Read the context number

Context is the information sent with a model request: instructions, tool
schemas, conversation, and eligible tool or image content. A model has a finite
window for that input and its answer.

The sidebar combines the previous request's measured usage with an estimate of
what changed since then. It is a projection for the next request, not a new
provider measurement. `/context` distinguishes the values and their breakdown.

Sigil keeps the saved session log intact while sending a derived view to the
model. The default `append-only` strategy preserves the ordinary request prefix
until compaction is needed. By default:

- At 60% of the window, it reduces eligible older payloads in a batch.
- If that remains above 40%, the session's model is asked for a checkpoint.
- The newest five reasoning blocks and twenty tool calls stay intact.
- An additional 5% margin is reserved for estimation uncertainty, along with
  space for the response.

These are policy defaults and estimates, not a guarantee of equivalent model
quality. If a protected request still won't fit, Sigil reports an error instead
of deleting user history. Append-only budgeting needs a known model window.
`passthrough` disables compaction and is intended for diagnostics.

## Compact explicitly

Wait until the session, its subagents, and pending inputs are idle. Manual
compaction requires the session service and the `append-only` strategy. Choose a
mode:

| Command                    | Mode                                   |
| -------------------------- | -------------------------------------- |
| `/compact`                 | Deterministic; the default             |
| `/compact --deterministic` | Explicit deterministic mode            |
| `/compact --llm`           | Ask the session model for a checkpoint |

Deterministic mode reduces eligible content without a model request. LLM mode
can make a provider request and incur cost, even if a checkpoint is later
rejected or doesn't reduce the projection. A short history may have nothing
eligible to compact. The result reports what happened; it doesn't mean the
original conversation was erased.

## Read the cost number

Sigil uses provider-reported request cost when available. Otherwise it estimates
from known list prices. The symbols distinguish those cases:

| Display   | Meaning                                         |
| --------- | ----------------------------------------------- |
| `$1.20`   | All priced requests used provider-reported cost |
| `~$0.04`  | At least some pricing is a list-price estimate  |
| `$0.04+`  | A lower bound: some requests couldn't be priced |
| `~$0.04+` | Estimated and incomplete                        |
| `?`       | No requests could be priced                     |
| `$0`      | No requests have been sent yet                  |

Amounts below one cent retain extra decimals rather than appearing free. A
subscription may not charge per token even when the estimator shows a token
list-price equivalent. Discounts, reseller prices, and plan rules can differ.
Use your provider's billing information for authoritative charges.

Cost is derived from saved usage and survives reopening the session. Automatic
titles and maintenance requests can add usage beyond the main answer. Unknown
prices aren't silently treated as zero.

## Revisit an older image

Images are bounded before sending and may leave the active request view after a
few rounds or when their pixel budget is exceeded. They remain in the saved
session; refer to the image marker again when you need the agent to inspect one.
File size alone doesn't determine image-token cost.

## Inspect diagnostics when needed

Use `/debug --text` for numeric diagnostics or **Alt+D** for the debug view.
Cache reads and writes are separate: a write isn't a hit, and absent reporting
isn't proof of zero cache reuse. Debug distinguishes recorded usage from a
projection; it doesn't display conversation or tool-payload previews.

Startup policy settings are in the
[environment reference](../reference/environment.md). For algorithms, estimation
limits, and reproduction, read the
[technical context reference](../../technical/internals/context.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
