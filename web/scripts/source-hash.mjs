/**
 * The one recipe for "is the committed bundle still the source?".
 *
 * `web/dist` is committed so the server runs without Node, which makes staleness
 * the only failure mode worth guarding: someone edits `src/`, commits, forgets to
 * rebuild, and the browser serves the previous UI. The guard is a hash of every
 * input that can change the output, written into `dist/build.json` at build time
 * and recomputed before a commit and in the test suite.
 *
 * The recipe is duplicated in Python (`src/server/webbuild.py`) because the server
 * must not depend on Node to answer. `tests/test_web_dist.py` compares both
 * implementations when Node is available, so the duplication cannot drift
 * silently — that test is the contract, not this comment.
 *
 * Changing this file changes the hash of everything, which is correct: a new
 * recipe means the previous `build.json` no longer describes the bundle.
 */

import { createHash } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

/** Directories under `web/` that are inputs. Order is irrelevant: everything is sorted. */
export const INPUT_DIRECTORIES = ['src', 'public'];

/** Files under `web/` that are inputs when they exist. */
export const INPUT_FILES = [
  'index.html',
  'package.json',
  'package-lock.json',
  'vite.config.ts',
  'tsconfig.json',
  'tsconfig.app.json',
  'tsconfig.node.json',
];

/** Never inputs, even if they appear under an input directory. */
export const IGNORED_DIRECTORIES = new Set(['node_modules', 'dist', '.vite']);

/** Characters of the digest kept in `build.json`. Enough to compare, short enough to read. */
export const HASH_CHARS = 16;

export const webRoot = fileURLToPath(new URL('..', import.meta.url));

async function walk(root, directory, found) {
  let entries;
  try {
    entries = await readdir(join(root, directory), { withFileTypes: true });
  } catch (error) {
    if (error.code === 'ENOENT') return found;
    throw error;
  }
  for (const entry of entries) {
    if (IGNORED_DIRECTORIES.has(entry.name)) continue;
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      await walk(root, path, found);
    } else if (entry.isFile()) {
      found.push(path);
    }
  }
  return found;
}

/**
 * Every input file, relative to `web/` and slash-separated.
 *
 * Slash-separated so the digest is identical on Windows and on Linux: a committed
 * bundle must not look stale because someone cloned the repo on another OS.
 */
export async function inputFiles(root = webRoot) {
  const found = [...INPUT_FILES];
  for (const directory of INPUT_DIRECTORIES) {
    await walk(root, directory, found);
  }
  const existing = [];
  for (const path of found) {
    try {
      const content = await readFile(join(root, path));
      existing.push({ path: path.split(sep).join('/'), content });
    } catch (error) {
      if (error.code === 'ENOENT') continue;
      throw error;
    }
  }
  return existing.sort((left, right) => (left.path < right.path ? -1 : left.path > right.path ? 1 : 0));
}

/**
 * The digest of the inputs: a sorted list of `path\0sha256(content)` lines.
 *
 * The path is part of the input, so renaming a file with identical content still
 * invalidates the bundle — the browser fetches by name.
 */
export async function sourceHash(root = webRoot) {
  const digest = createHash('sha256');
  for (const { path, content } of await inputFiles(root)) {
    const fileDigest = createHash('sha256').update(content).digest('hex');
    digest.update(`${path}\0${fileDigest}\n`);
  }
  return digest.digest('hex').slice(0, HASH_CHARS);
}

/** The hash stored in the last build, or `null` when the bundle was never built. */
export async function builtHash(root = webRoot) {
  try {
    const raw = await readFile(join(root, 'dist', 'build.json'), 'utf8');
    const parsed = JSON.parse(raw);
    return typeof parsed.hash === 'string' ? parsed.hash : null;
  } catch {
    return null;
  }
}

/** Whether `dist/` matches the current sources. */
export async function isFresh(root = webRoot) {
  const stored = await builtHash(root);
  if (stored === null) return false;
  return stored === (await sourceHash(root));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const root = process.env.SIGIL_WEB_ROOT ?? webRoot;
  process.stdout.write(`${await sourceHash(root)}\n`);
}
