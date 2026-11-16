# Install and launch Sigil

Run Sigil from a source checkout, then launch it from the project you want it to
work on. The directory you launch it in is the workspace; it doesn't have to be
Sigil's own repository.

## Before you start

You need these prerequisites:

- A Unix-like environment with Bash for the `sigil` launcher. These commands
  target Linux and macOS, not a native Windows shell.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) on `PATH`.
- Python 3.14 or newer. If needed, install it with `uv python install 3.14`.
- Git to clone the repository and use worktrees or file rollback.
- Provider-issued credentials or a supported browser sign-in flow. You connect
  the account after launch; no Sigil account is required.

You don't need Node to use the terminal or a matching committed web bundle. The
experimental Python execution tool has additional Linux requirements; it isn't a
prerequisite for ordinary conversation and file tools.

## Install the checkout

Choose a directory for Sigil and fetch its dependencies:

```sh
git clone https://github.com/GabrielMartinMoran/sigil.git
cd sigil
uv sync
```

Keep this checkout: the launcher uses its environment and source files.
`uv sync` prepares the environment; launching through `uv` also checks it.

## Open your project

From the directory you want the agent to work in, run the checkout's launcher.
Replace `/path/to/sigil` with the absolute path from the previous step:

```sh
cd ~/projects/my-project
/path/to/sigil/sigil
```

The terminal interface opens. On a fresh install, the sidebar may say the model
isn't configured; that is expected. Continue with
[your first conversation](first-conversation.md) to add an account.

Startup doesn't create a project configuration directory in the workspace. Your
conversation state is stored under `~/.sigil` by default. Once you ask the agent
to work, its tools can change workspace files.

## Optional: put the launcher on PATH

Create a symlink so you can type `sigil` from any project:

```sh
mkdir -p ~/.local/bin
ln -s /path/to/sigil/sigil ~/.local/bin/sigil
```

Make sure `~/.local/bin` is on your shell's `PATH`. If the destination already
exists, inspect it before replacing it. The launcher resolves symlinks without
changing the workspace directory.

## Know the first-launch side effect

The launcher enables the tracked Git hooks in **Sigil's own checkout** by
setting that clone's `core.hooksPath` to `.githooks`. It doesn't enable them in
a different project you open. Set `SIGIL_NO_HOOKS=1` before launching to opt
out. The [hook guide](../../technical/development/git-hooks.md) explains their
behavior.

## Next steps

Connect an account and choose a model in
[Your first conversation](first-conversation.md). If launch fails, use
[Troubleshooting](../troubleshooting/README.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
