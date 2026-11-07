#!/usr/bin/env node
/**
 * Points this clone at the tracked hooks, from the client's toolchain.
 *
 * The Python installer is the source of truth, but it needs an interpreter that
 * may not be resolved yet while `npm ci` runs; this script does the one thing a
 * clone is missing — `core.hooksPath` — and leaves the rest to
 * `scripts/install_hooks.py` (executable bits, diagnostics).
 *
 * It runs from `prepare`, which covers the clone where someone goes straight to
 * the client and never launches Sigil. Silence is the contract: a tarball has no
 * `.git`, a CI checkout has no reason to install hooks, and neither should print
 * a warning.
 */

import { execFileSync } from 'node:child_process';
import { chmodSync, existsSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HOOKS_DIR = '.githooks';

/** The repository root: this file lives in `web/scripts/`. */
const root = fileURLToPath(new URL('../..', import.meta.url));

function git(args) {
  return execFileSync('git', args, {
    cwd: root,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'ignore'],
  }).trim();
}

if (process.env.SIGIL_NO_HOOKS) process.exit(0);
if (!existsSync(join(root, '.git'))) process.exit(0);

let current = '';
try {
  current = git(['config', '--get', 'core.hooksPath']);
} catch {
  // Unset: `git config --get` exits 1, which is the case this script exists for.
}
if (current === HOOKS_DIR) process.exit(0);

try {
  git(['config', 'core.hooksPath', HOOKS_DIR]);
} catch {
  // A read-only config is not this script's problem: the gate still exists, it
  // just will not run in this clone.
  process.exit(0);
}

// Git preserves the executable bit in the object, but a checkout made with a
// restrictive umask can land without it, and a hook that is not executable is
// silently ignored.
const hook = join(root, HOOKS_DIR, 'pre-commit');
try {
  if (!(statSync(hook).mode & 0o100)) chmodSync(hook, 0o755);
} catch {
  // Missing hook: `scripts/install_hooks.py` reports that; nothing to do here.
}

process.stderr.write(`sigil: git hooks enabled (${HOOKS_DIR}) — SIGIL_NO_HOOKS=1 opts out.\n`);
