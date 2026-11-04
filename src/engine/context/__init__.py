"""Context management for Sigil.

Three pieces with different contracts:

- **Log**: the full session history, append-only. It is what gets persisted.
- **View**: `ContextStrategy.view(log, budget)`, deterministic and non-destructive.
  It is what the model sees on every request.
- **Measurement**: `ContextMeter`, with the measured number (ground truth of the last
  request) and the projected one (what the next one will weigh).

See `proposals/context-management.md` for the design and the decisions.

The submodules are the import points: no symbols are re-exported here.
"""
