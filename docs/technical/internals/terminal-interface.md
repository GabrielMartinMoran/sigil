# Terminal presentation contracts

This record preserves the terminal's interaction and rendering rationale. For
task-oriented controls, use the
[terminal guide](../../usage/guides/terminal.md). Shared service state is
covered by the [terminal-client record](../architecture/terminal-client.md).

## Interactive questions

`ask_user` asks 2 or 3 options per question —the recommended one appears marked—
plus a free field to type another answer. The agent uses it when the
requirements are ambiguous or an action needs explicit authorization.

The questions appear **in place of the composer**, across the whole width, right
above the status bar: not in a modal, and not in a box inside the conversation.
The box that owns the keyboard is the box on screen — a visible composer that
cannot be typed into is a lie, and it was the most prominent thing on the
screen.

While a question is open the conversation stays readable above it, and **both
columns give up height, not their place**: the panel is a sibling of the row
that holds the transcript and the side panel, so it takes its room from that
whole row. The side panel keeps its width and scrolls, so a shorter one is still
usable — and its roadmap is often exactly what the question is about. A
half-typed message survives too: the composer is hidden, not emptied, and it
returns focused with your text.

While a question is open **the panel owns the keyboard**. Clicking the
conversation or the panel's own frame to read something does not take the keys
away: the arrows keep moving the highlight and `Enter` keeps answering.
Answering is the only thing the turn is waiting on, so it is what the keyboard
is for; the composer takes the keys back the moment the question closes.

Each question keeps its own answer. Moving between them never loses anything:

| Key                 | What it does                                                |
| ------------------- | ----------------------------------------------------------- |
| `↑` / `↓`           | Moves the highlight in the list                             |
| `Enter`             | Answers with the highlighted option, or with the typed text |
| `alt+←` / `alt+→`   | Goes back and forward between questions                     |
| `Tab`               | Moves between the list and the free field                   |
| `ctrl+↑` / `ctrl+↓` | Scrolls the conversation without leaving the panel          |
| `Esc`               | Stops the turn: the panel closes and the prompt comes back  |

Going back shows what you chose: the option you picked appears with a `✓`, the
text you typed is still in the field, and the panel keeps a short summary of
what was already answered. The questions close once all of them have an answer,
and if you try to finish early, the panel takes you to the one that is still
missing.

Each option is one line, clipped with `…` when the terminal is narrow, and the
clipped part is always the description: the label and the `[recommended]` tag
are never the part that goes. If the panel runs out of height it scrolls, but
the key legend underneath stays pinned and visible — a question you cannot
finish reading is a question you cannot answer.

`Esc` is the way out of a question you do not want to answer, so it ends the
turn instead of picking something for you: the agent stops where it is, the chat
shows `Turn cancelled.` and control goes back to you. Nothing is lost: the
messages already exchanged stay in the session, the unanswered call is closed
out, and the next message starts from a valid history.

Here `Esc` is a single press, not the two-press confirmation that stops a plain
turn: the panel is already asking you something, and adding a confirmation would
mean answering one question in order to decline another.

That single press works from anywhere while the question is open. If the key
does not reach the panel — nothing is focused, or a widget in between took it —
the app asks the panel first, so `Esc` cancels the question instead of arming
the abort. The two are very different outcomes: cancelling closes the question
and stops the turn cleanly, while the abort leaves the question on screen with a
run that is no longer there to read the answer.

Cancelling is not an error and not an answer. The run stops, so the model never
gets to react to the cancellation and never decides what to do next on its own.

### Roadmap

`update_roadmap` maintains the task list at the end of the sidebar. Each item
stays in `pending`, `in_progress`, `done` or `blocked`.

The panel shows a single list in plan order, without grouping by status. The
icon and the color are enough to read it at a glance:

| Status        | Icon | Style                                                        |
| ------------- | ---- | ------------------------------------------------------------ |
| `done`        | `✓`  | green; completed items remain readable without strikethrough |
| `in_progress` | `▶`  | blue                                                         |
| `pending`     | `○`  | white                                                        |
| `blocked`     | `!`  | yellow                                                       |

The roadmap belongs to the agent that executes it, not to the directory: the
root has the plan of the session and a subagent plans for its own task. The
state is rows in `~/.sigil/sigil.db`, keyed by execution. If the write fails,
the list keeps working in memory and the error stays in `persistence_error`.

The list scrolls with the panel, so a long plan never grows the sidebar past the
screen. It does not scroll to follow the agent: the plan is read as a whole, and
a list that jumped every time an item changed state would be unreadable at
exactly the moment it is worth reading.

### The composer

`Enter` sends the message and `Shift+Enter` adds a line. The composer grows with
the message up to ten rows and then scrolls, so a pasted stack trace does not
push the conversation off the screen.

`Shift+Enter` needs a terminal that reports modifiers (the Kitty keyboard
protocol, which Textual enables on capable terminals). On one that does not, the
terminal sends a bare carriage return and the key reads as `Enter`, so the
message is sent instead of split. That is the same behaviour every other
line-oriented chat has, and it fails towards the familiar thing rather than
towards a lost keystroke.

### Pasting an image

`ctrl+v` with an image on the clipboard attaches it to the message. A terminal
carries text and nothing else, so the bytes are read from the desktop, and the
composer writes `[Image #1]` where the cursor was:

```text
› explain what breaks in this diff [Image #1] and compare it with the test
```

The marker is the message: the model reads that text and the picture beside it,
in the same order, so "the second screenshot" means what it says. It is painted
in reverse video to stand out from what you typed, and it is deleted as **one
block**: a single `backspace` removes the whole marker, the image goes with it,
and the remaining markers are renumbered, so `[Image #2]` never survives the
deletion of `[Image #1]`.

Each image is bounded before it is sent: the longest side is scaled to 1568 px,
a PNG stays a PNG (recompressing a terminal screenshot as JPEG fuzzes the text),
a JPEG stays a JPEG, and anything above 4 MB is compressed further. The chat
shows what travelled: `▣ 1 image (1280×720 PNG)`.

**Clipboard tools.** The bytes come from the desktop, and each one needs its
own:

| Desktop | Needs                 | Install                    |
| ------- | --------------------- | -------------------------- |
| X11     | `xclip`               | `apt install xclip`        |
| Wayland | `wl-paste`            | `apt install wl-clipboard` |
| macOS   | `pngpaste`            | `brew install pngpaste`    |
| Windows | PowerShell (built in) | —                          |

If the tool is missing, the composer says so once and pastes text as usual: the
feature degrades to the behaviour that existed before, it does not break
pasting. Wayland is detected from `XDG_SESSION_TYPE` (or `WAYLAND_DISPLAY` when
the session type is not declared) and never falls back to `xclip`, which in an
XWayland session answers from a stale X selection.

**Only multimodal models.** A model whose catalogue declares text only refuses
the paste with a line naming `/model`; the turn is not spent on a 400. A
catalogue that says nothing is assumed capable, because refusing a paste the
model would have accepted is the worse mistake. The flag travels with the saved
selection, so a cold start decides without reaching the network.

An image pasted mid-turn (see
[Sending a message mid-turn](turn-lifecycle.md#sending-a-message-mid-turn)) goes
into the running turn like any other message.

**What it costs.** The context meter prices an image by its pixels — patches of
28×28 — not by the size of the file, and images from the turns the view has
moved past are dropped from the request with a note where they were, because a
screenshot costs full price forever while being useful for about one turn. They
stay in the session on disk: the note says so, and asking about one again
re-attaches it.

### Status bar

The line under the composer answers four questions, left to right.

**Who?** The **agent in effect**, always visible. It is the one piece of context
that changes how every answer reads, and having to open a modal to remember
which agent is talking is exactly the confusion the bar removes.

**Is it working?** A spinner and the verb of the moment — `Thinking`, `Writing`,
`Running read_file` — with the elapsed time once a turn passes a second. Between
turns it rests on `● Ready`. Without it, an app waiting on the model looks
exactly like an app waiting on you.

**Where?** The worktree and the branch the agent is working on: `main main` for
the main worktree, `fix-login sigil/fix-login` for a linked one. With worktrees
in the picture, two sessions look the same and write to different trees.

**How much?** `+18 −4  1 new`: the added and removed lines of the working tree
in the green and red `git diff` uses, plus a count of the files git does not
track yet. It comes from `git diff --numstat HEAD`, so staged and unstaged
changes are counted together. Untracked files are a count and not a line count,
because `git diff` has no diff for a file outside the index and any number there
would be an invention.

```
agent Wizard  ·  ⠋ Thinking 12s           main sigil/refactor-ui  +18 −4  1 new
```

A transient message (`Press Esc again to stop the agent`) takes the place of the
activity group for as long as it is up, so the bar does not grow a second line
for two words. It never takes the agent: what answers is not transient.

The summary is read off the event loop after every tool call and on a 1.5-second
poll; a tool can change the tree through a shell command the file tools never
see, so there is no event to subscribe to. Outside a git repository the section
does not appear at all.

### Sidebar

The sidebar is one panel with the sections the session needs, in the order they
are read. The diagnostics —what the agent is working with— come first, and the
roadmap of the task closes the list: they are read together, so a second box
under the first only cost a border, a margin and the height of a column that sat
mostly empty.

The sections, from top to bottom:

- **Model**: the active account alias, its provider family, the model, the
  reasoning level in effect, the context budget of that model's window, and the
  cost of the session so far. Before anything is configured it says
  `not configured — /provider`, which is the first thing to do on a fresh
  install.
- **Loaded skills**: the skills the agent loaded in this session, in load order.
  It is the quick diagnostic of whether it is following the protocol.
- **MCP**: the count goes inline with the title, in parentheses. Below, each
  server is listed with the same `•` the skills above it use: green for the ones
  that work, `✗` in red for the ones that do not. With no configured servers,
  the section does not appear.
- **Roadmap**: the plan of the task, one line per item, with its notes. It is
  the last section, and the one read after every message.

The context budget is the last line of the model section and not a section of
its own: it is a property of that window, it is read with the model, and a
heading for one number cost two rows of a 34-column panel. It reads like the
line above it, and the bill of the session closes the same block:

```
Model
 work · OpenRouter
 anthropic/claude-sonnet-4
 effort: high
 context: 66% (132K / 200K)
 cost: $0.04

Loaded skills
 • clean-code
 • frontend-design

MCP (3, 1 ✗)
 • github
 • playwright
 • filesystem
 ✗ broken

Roadmap
 ▶ Rewrite the tool renderer
 ✓ Read the current one
 ○ Update the tests
```

The percentage colours green, yellow and red across 70% and 90%, and the pair it
comes from rides in parentheses. The tokens are compacted so it fits in the
column: `K` for thousands and `M` for millions, no decimals once the number is
`>= 100K` and never for millions. Below 100K a single decimal survives when it
says something —`12.4K`, `5.5K`— and disappears when it would read `.0`.

The whole panel scrolls, not just the roadmap. With one box there is one
scrollbar: nesting a scrolling list inside a scrolling panel would put a
scrollbar inside a scrollbar in a column that is 34 characters wide. A plan of
thirty items and twenty loaded skills stay inside the panel instead of pushing
the composer off the screen.

`/sidebar off` hides the panel, and `/sidebar on` brings it back. Hiding does
not erase anything: the agent keeps updating the roadmap while it is out of
view.

The MCP count refreshes on startup (in a worker, without blocking the UI) and
every time you run `/mcp`.

#### Session cost

The `cost:` line is what the session has spent so far. It comes from the
provider whenever the provider reports it: OpenRouter sends the amount it is
going to charge for a request, and that number wins, because it is the invoice
and not an estimate. Every other request is priced by `genai-prices`, the
list-price table pydantic-ai ships. The panel marks that case with `~`, since a
list price is not a charge.

A model no table knows is left out rather than counted as zero, and a session
with such a request shows `+`: the amount is a floor.

| Line            | Meaning                                                                           |
| --------------- | --------------------------------------------------------------------------------- |
| `cost: $1.20`   | Every request was priced by the provider: this is the bill.                       |
| `cost: ~$0.04`  | Priced at list price. A reseller, a discount or a plan can charge something else. |
| `cost: $0.04+`  | At least this much. Something in the session could not be priced.                 |
| `cost: ~$0.04+` | Both: a list price, and incomplete.                                               |
| `cost: ?`       | Nothing in the session could be priced at all.                                    |
| `cost: $0`      | Nothing has been sent yet.                                                        |

Amounts under a cent keep four decimals, because that is what the first requests
of a session cost and `$0.00` would read as free. Above a cent they are rounded
to cents.

A subscription is the case no estimator can answer: signing in with a ChatGPT
plan bills nothing per token, so the line shows what those tokens would have
cost at the published rate. That comparison is what most tools show their
subscribers, and it is why the amount carries a `~` rather than a promise.

The number is calculated from the session history, so it survives `/load`: a
restored conversation shows the bill its own requests produced, not zero.
`/context` prints the same figure with the counts behind it — how many requests
were priced by the provider, how many came from the table, and how many could
not be priced.

## Reading the chat

Reasoning, tool summaries, and expanded results have separate presentation
rules.

### Reasoning

The model's reasoning is **collapsed by default**. It is the part of the
transcript nobody reads twice, and expanded it buries the answer. The collapsed
turn still says it thought, how much, and how to see it:

```
✳ Reasoning · 42 lines  /reasoning-visibility on
```

`/reasoning-visibility on` expands every block in place — the conversation on
screen is not rebuilt, so a turn in flight keeps its pending deltas — and
`/reasoning-visibility off` collapses them again. The choice is stored in
`~/.sigil/ui.json` and applies to the next session too.

### Tool calls

A tool call and its result are one row, and the row is the summary:

```
  │ 4 tools · 610ms
  │ ✓ patch tests/core/test_roadmap.py  3 replacements · 24ms
  │ ✓ run ['pytest tests/core/test_roadmap.py -q']  9 passed in 0.45s · 450ms
  │ ✓ run ['pytest tests/test_per_…  1 failed, 8 passed in 0.61s · 610ms
  │ ↳ 1 failed
  │ ✓ read tests/core/test_roadmap.py  2 of 30 lines · 3ms
```

The gutter is what groups them: a run of calls reads as one column hanging under
the answer that caused it, instead of a list of unrelated lines that all start
at the left margin. Consecutive calls share no gap, so the column is continuous.

- **Two axes, deliberately apart.** The glyph says whether the _call_ came back:
  `…` while it runs, `✓` when it returned, `✗` when it raised. The color of the
  summary says how it _went_, and a failing test run is a successful call with a
  red result — not a broken tool. Painting both with `✗` would make a red test
  look like a crash.
- **The right-hand side is the tool's own sentence**, not `str(result)`. Each
  tool declares how to summarize itself, because only the tool knows that
  `3 replacements` is the interesting fact about a patch and `bytes_before` is
  not. A tool that declares nothing shows nothing there; the row is still
  honest.
- The header over a run of two or more calls says what the step is and how far
  along it is: `⋯ 3 tools [1/3]` while it runs, `4 tools · 1 error · 610ms` when
  it is over. A batch of one call needs no header and does not get one.
- **The output is collapsed by default**, like the reasoning, and for the same
  reason: the row already says what came back. `… 12 lines` under a row that
  reads `9 passed in 0.45s` is a second line for nothing.
- **A failure is never collapsed.** A tool that raised shows its error, and a
  call whose result came back bad shows the first lines that explain it
  (`↳ 1 failed`). Hiding a failure behind a preference nobody has set yet would
  hide the one thing there is to act on.
- `/tools expanded` shows the output of every call, on screen and in the ones to
  come; `/tools collapsed` puts it back. The choice is stored in
  `~/.sigil/ui.json` and survives the next session.
- A dict of arguments is rendered as `key=value` pairs; a single argument is
  shown on its own, which is what makes `run(command='ls')` read as `run ls`.
- The row is composed to the width it has: the result sits flush right, the
  arguments are clipped with `…`, and when there is no room for both the result
  is dropped before the call is squeezed into an unreadable stub.
- A name shorter than the callable's: `write`, `patch`, `read`, `run`, `search`.
  An MCP tool keeps its full name, which is also what makes an unfamiliar tool
  look unfamiliar.
- Expanding keeps the output's leading whitespace, because a diff, a stack trace
  or a snippet of code is unreadable without it. Past six lines the block
  truncates with `… +N more`, and past 160 characters a long line truncates with
  an ellipsis. Enough to recognize the result, never enough to take over the
  screen.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
