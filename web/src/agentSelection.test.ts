import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';

import { useStore } from './store';
import type { State } from './types';

const originalFetch = globalThis.fetch;
const initial = { agent: { name: 'wizard' }, agents: [], busy: false } as unknown as State;
let calls: { path: string; body: unknown }[];
let refused: boolean;

beforeEach(() => {
  calls = [];
  refused = false;
  useStore.setState({ state: initial, busy: false, error: '', selectingAgent: false });
  globalThis.fetch = (input, init) => {
    assert.ok(typeof input === 'string');
    assert.ok(init?.body === undefined || typeof init.body === 'string');
    const path = input;
    calls.push({ path, body: init?.body ? JSON.parse(init.body) : null });
    if (path === '/api/agent') {
      return Promise.resolve(
        new Response(JSON.stringify(refused ? { error: 'Cannot switch agent while busy.' } : {}), {
          status: refused ? 409 : 200,
        }),
      );
    }
    assert.equal(path, '/api/state');
    return Promise.resolve(new Response(JSON.stringify({ ...initial, agent: { name: 'reviewer' } })));
  };
});
afterEach(() => {
  globalThis.fetch = originalFetch;
});

void test('selecting an agent posts the choice and refreshes authoritative state', async () => {
  assert.equal(await useStore.getState().selectAgent('reviewer'), true);
  assert.deepEqual(calls, [
    { path: '/api/agent', body: { name: 'reviewer' } },
    { path: '/api/state', body: null },
  ]);
  assert.equal(useStore.getState().state?.agent?.name, 'reviewer');
  assert.equal(useStore.getState().selectingAgent, false);
});

void test('refused selection keeps the previous profile and reports the server error', async () => {
  refused = true;
  assert.equal(await useStore.getState().selectAgent('reviewer'), false);
  assert.equal(useStore.getState().state?.agent?.name, 'wizard');
  assert.match(useStore.getState().error, /Cannot switch agent/);
  assert.equal(useStore.getState().selectingAgent, false);
});

void test('busy or pending selection does not submit another request', async () => {
  useStore.setState({ busy: true });
  assert.equal(await useStore.getState().selectAgent('reviewer'), false);
  useStore.setState({ busy: false, selectingAgent: true });
  assert.equal(await useStore.getState().selectAgent('reviewer'), false);
  assert.deepEqual(calls, []);
});
