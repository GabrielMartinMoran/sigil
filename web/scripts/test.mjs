import { spawnSync } from 'node:child_process';
import { mkdtemp, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { build } from 'vite';

// Reuse the project's TypeScript bundler and Node's test runner, without a browser.
const root = fileURLToPath(new URL('..', import.meta.url));
const tests = (await readdir(join(root, 'src'))).filter((name) => name.endsWith('.test.ts'));
const output = await mkdtemp(join(tmpdir(), 'sigil-web-tests-'));
try {
  await build({
    configFile: false,
    logLevel: 'error',
    build: {
      outDir: output,
      emptyOutDir: false,
      lib: {
        entry: Object.fromEntries(tests.map((name) => [name.replace(/\.ts$/, ''), join(root, 'src', name)])),
        formats: ['es'],
        fileName: (_format, name) => `${name}.mjs`,
      },
      rollupOptions: { external: [/^node:/] },
    },
  });
  const result = spawnSync(
    process.execPath,
    ['--test', ...tests.map((name) => join(output, name.replace(/\.ts$/, '.mjs')))],
    {
      stdio: 'inherit',
      timeout: 30_000,
    },
  );
  process.exitCode = result.status ?? 1;
} finally {
  await rm(output, { recursive: true, force: true });
}
