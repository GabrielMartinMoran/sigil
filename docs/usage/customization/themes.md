# Choose or create a terminal theme

Use `/theme` to change the terminal's colors with a live preview. Custom themes
are JSON files; they don't require a code change or a restart to appear in the
picker. This guide concerns the terminal, not browser styling.

## Choose an installed theme

Run `/theme`, move the highlight, and preview the result in the actual app.
**Enter** keeps the selected theme; **Esc** restores the one active when you
opened the picker. Use `/theme --list` to see installed themes and load errors,
or `/theme synthwave` to apply one directly.

Sigil includes three themes:

| Theme       | Appearance                                                         |
| ----------- | ------------------------------------------------------------------ |
| `sigil`     | Obsidian surfaces, warm beige text, amber and burnt-orange accents |
| `dark`      | Neutral dark surfaces with a cool accent                           |
| `synthwave` | Hot pink, cyan, and violet on deep purple                          |

The selection is saved. The picker rereads the theme directory when opened, so a
newly added file doesn't require restarting the app.

## Copy a template

From Sigil's checkout, create the destination directory and copy a template:

```sh
mkdir -p ~/.sigil/themes
cp themes/synthwave.json ~/.sigil/themes/my-theme.json
```

Edit `my-theme.json`, change its `name` to `my-theme`, and adjust its colors.
Templates remain in the repository's [`themes/`](../../../themes/) directory;
this page is their configuration guide.

The default installation directory is `<SIGIL_HOME>/themes`. The commands above
assume the default `SIGIL_HOME`. `SIGIL_THEMES_DIR` overrides the theme
directory independently, for example when keeping themes in a dotfiles
repository.

## Define a theme

At minimum, give the file a name and a primary color. A fuller example is:

```json
{
  "name": "my-theme",
  "dark": true,
  "colors": {
    "primary": "#ff2a6d",
    "accent": "#05d9e8",
    "background": "#0d0221",
    "foreground": "#f8f8f2",
    "tool-border": "#4b2a6b",
    "reasoning": "#9d8ec7",
    "diff-added": "#01ffc3",
    "diff-removed": "#ff3864"
  }
}
```

Textual palette roles include `primary`, `secondary`, `accent`, `warning`,
`error`, `success`, `foreground`, `background`, `surface`, `panel`, and `boost`.
`primary` is required; omitted roles inherit defaults.

Additional names become CSS variables, including `tool-border`, `tool-title`,
`tool-output`, `reasoning`, `diff-added`, `diff-removed`, `sidebar-section`,
`muted`, `logo-start`, and `logo-end`. The welcome mark uses `logo-start` and
`logo-end`, falling back to `primary` and `secondary`.

## Check a theme that won't load

Run `/theme --list` and inspect the reported file. One invalid color rejects the
whole theme rather than applying it partially. Other valid files still load. A
user file with the same name as a built-in theme replaces it.

Preview text, selection states, failures, and diffs before keeping a palette.
Color mustn't be the only way a reader distinguishes status. For selection-tint
rationale, see the
[terminal presentation contract](../../technical/internals/terminal-interface.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
