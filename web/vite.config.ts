import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import { sourceHash, webRoot } from './scripts/source-hash.mjs';

/**
 * Writes `dist/build.json` with the hash of the inputs that produced it.
 *
 * `closeBundle` and not `buildStart`: the hash has to describe what was actually
 * emitted, and a build that fails must not leave a `build.json` claiming the
 * bundle is current. The file lands inside `outDir`, so it is part of the commit
 * the freshness guard compares against.
 *
 * It is only rewritten with a new timestamp when the hash changes. `emptyOutDir`
 * deletes `dist/` before the bundle is emitted, so the previous manifest is read
 * in `buildStart` and kept in the closure; when the hash matches, the file is
 * written back byte-identical and git sees no change.
 */
function buildManifest(): Plugin {
  let previous: string | null = null;
  return {
    name: 'sigil-build-manifest',
    apply: 'build',
    async buildStart() {
      previous = await readFile(join(webRoot, 'dist', 'build.json'), 'utf8').catch(() => null);
    },
    async closeBundle() {
      const hash = await sourceHash(webRoot);
      const manifestPath = join(webRoot, 'dist', 'build.json');
      let builtAt = new Date().toISOString();
      let kept = false;
      if (previous !== null) {
        try {
          const parsed = JSON.parse(previous) as { hash?: string; built_at?: string };
          if (parsed.hash === hash && typeof parsed.built_at === 'string') {
            builtAt = parsed.built_at;
            kept = true;
          }
        } catch {
          // A corrupt manifest is replaced: that is the point of the rewrite.
        }
      }
      const manifest = { hash, built_at: builtAt, node: process.version };
      await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
      this.info(`build.json ${kept ? 'unchanged' : 'written'} (hash ${hash})`);
    },
  };
}

export default defineConfig({
  // Relative assets: the bundle is served from `/` by the Sigil server, but a
  // reverse proxy that mounts it under a prefix must keep working.
  base: './',
  plugins: [react(), tailwindcss(), buildManifest()],
  server: {
    port: 5173,
    // `npm run dev` talks to the Python server for the API and the event stream.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8760', changeOrigin: false },
      '/events': { target: 'http://127.0.0.1:8760', changeOrigin: false, ws: false },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // A stale source map in a committed bundle is dead weight: the server serves
    // what is in the repo, not a development build.
    sourcemap: false,
    target: 'es2022',
  },
});
