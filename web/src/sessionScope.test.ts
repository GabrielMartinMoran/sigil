import assert from 'node:assert/strict';
import test from 'node:test';

import { api } from './api';

void test('session scope is encoded on HTTP reads and actions', async () => {
  const original = globalThis.fetch;
  const urls: string[] = [];
  globalThis.fetch = (input) => {
    assert.equal(typeof input, 'string');
    urls.push(input as string);
    return Promise.resolve(new Response('{}', { status: 200 }));
  };
  try {
    await api.state('session/a');
    await api.transcript(20, 5, 'session/a');
    await api.abort('session/b');
    assert.deepEqual(urls, [
      '/api/state?session_id=session%2Fa',
      '/api/session/transcript?limit=20&before=5&session_id=session%2Fa',
      '/api/abort?session_id=session%2Fb',
    ]);
  } finally {
    globalThis.fetch = original;
  }
});

void test('old stream callbacks are rejected when only the selected ID changes', async () => {
  const { isCurrentSession } = await import('./sessionSelection');
  assert.equal(isCurrentSession({ selectedSessionId: 'old', selectionGeneration: 3 }, 'old', 3), true);
  assert.equal(isCurrentSession({ selectedSessionId: 'new', selectionGeneration: 3 }, 'old', 3), false);
  assert.equal(isCurrentSession({ selectedSessionId: 'old', selectionGeneration: 4 }, 'old', 3), false);
});
