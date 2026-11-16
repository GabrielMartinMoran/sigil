"""Git worktrees as alternative workspaces.

A *workspace* is a directory Sigil works in: the main worktree of a git
repository or one of its linked worktrees. The repository (`GitRepository`)
knows how to discover them and how to create new ones; the `WorkspaceManager`
owns the active one and re-points the components that cache its root.

The package re-exports no symbols: import from the owning module.
"""
