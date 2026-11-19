import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';

import { useStore } from './store';

let calls: { path: string; body: unknown }[] = [];
let refuse = false;

beforeEach(() => {
  calls = [];
  refuse = false;
  useStore.setState({ selectedSessionId: 'session-1', pendingSend: null, busy: false, notice: '', error: '' });
  globalThis.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
    calls.push({ path, body: typeof init?.body === 'string' ? JSON.parse(init.body) : null });
    if (path.startsWith('/api/goal')) {
      const body = refuse ? { error: 'Question pending.' } : { notice: 'Goal set.' };
      return Promise.resolve(new Response(JSON.stringify(body), { status: refuse ? 409 : 200 }));
    }
    if (path.startsWith('/api/state')) {
      return Promise.resolve(new Response(JSON.stringify({ session: { id: 'session-1' }, busy: false })));
    }
    return Promise.resolve(new Response(JSON.stringify({ accepted: 'turn' })));
  };
});

void test('goal commands use the goal endpoint without starting a turn', async () => {
  assert.equal(await useStore.getState().send('/goal --yolo Ship CSV'), true);
  assert.deepEqual(calls[0], { path: '/api/goal?session_id=session-1', body: { argument: '--yolo Ship CSV' } });
  assert.equal(
    calls.some((call) => call.path.startsWith('/api/turn')),
    false,
  );
  assert.equal(useStore.getState().notice, 'Goal set.');
});

void test('a refused goal command preserves the composer text', async () => {
  refuse = true;
  assert.equal(await useStore.getState().send('/goal --yolo Ship CSV'), false);
  assert.equal(useStore.getState().error, 'Question pending.');
});
