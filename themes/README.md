# Theme templates

The three builtin themes, as files. They are the same colors the app ships with, and
they exist so you have a working file to copy instead of a shape to reconstruct:

```sh
cp themes/synthwave.json ~/.sigil/themes/mine.json
$EDITOR ~/.sigil/themes/mine.json   # change "name" and the colors
```

`/theme --list` shows what loaded and what did not; `/theme` re-reads the folder
before opening the picker, so no restart is needed.

A file only needs a `name` and a `primary` color; everything else is inherited from
Textual's defaults. The keys are documented in the README, under **Themes**.
