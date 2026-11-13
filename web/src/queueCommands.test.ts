import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';

import { durableSections, useStore } from './store';
import type { DurableEntry, DurableSession } from './types';

type Call = { path: string; body: Record<string, unknown> };

let calls: Call[] = [];
let failTurn = false;
let durableBody: DurableSession = emptyDurable();
let durableCalls = 0;
let durableStatus = 200;
let durableError = 'Session not stored.';

function entry(request_id: string, sequence: number, state: DurableEntry['state'], delivered = false): DurableEntry {
  return {
    request_id,
    sequence,
    kind: 'turn',
    state,
    text: request_id,
    image_count: 0,
    delivered,
    checkpointed: delivered ? true : null,
    destination_id: delivered ? 'session' : null,
    destination_available: delivered,
  };
}

function emptyDurable(): DurableSession {
  return {
    session: { id: 'session', title: null, updated_at: 'now', preview: '' },
    transcript: [],
    skills: [],
    queue: [],
    pending: [],
    deliveries: [],
    recoveries: [],
    conflicted_entries: [],
  };
}

function reset(): void {
  calls = [];
  durableCalls = 0;
  failTurn = false;
  durableBody = emptyDurable();
  durableStatus = 200;
  durableError = 'Session not stored.';
  useStore.setState({
    items: [],
    durable: null,
    pendingSend: null,
    busy: false,
    error: '',
    notice: '',
    stream: { text: '', thinking: '', tools: [], batch: 0, afterTools: false },
  });
  globalThis.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.pathname : '';
    if (path === '/api/turn') {
      const raw = typeof init?.body === 'string' ? init.body : '{}';
      const body = JSON.parse(raw) as Record<string, unknown>;
      calls.push({ path, body });
      if (failTurn) return Promise.resolve(new Response(JSON.stringify({ error: 'nope' }), { status: 500 }));
      return Promise.resolve(
        new Response(JSON.stringify({ accepted: 'turn', request_id: body.request_id }), { status: 200 }),
      );
    }
    if (path === '/api/session/durable') {
      durableCalls++;
      if (durableStatus !== 200) {
        return Promise.resolve(new Response(JSON.stringify({ error: durableError }), { status: durableStatus }));
      }
      return Promise.resolve(new Response(JSON.stringify(durableBody), { status: 200 }));
    }
    if (path.startsWith('/api/session/transcript')) {
      return Promise.resolve(
        new Response(
          JSON.stringify({ items: [], first_index: 0, has_more: false, total: 0, skills: [], pending: [] }),
          { status: 200 },
        ),
      );
    }
    return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }));
  };
}

beforeEach(reset);

void test('a retry of the same send reuses its UUID', async () => {
  failTurn = true;
  assert.equal(await useStore.getState().send('hello'), false);
  const first = calls[0]?.body.request_id;
  assert.equal(typeof first, 'string');

  failTurn = false;
  assert.equal(await useStore.getState().send('hello'), true);
  assert.equal(calls[1]?.body.request_id, first);
  assert.equal(useStore.getState().pendingSend, null);
});

void test('changed content starts a new send with a new UUID', async () => {
  failTurn = true;
  assert.equal(await useStore.getState().send('one'), false);
  const first = calls[0]?.body.request_id;
  assert.equal(await useStore.getState().send('two'), false);
  assert.notEqual(calls[1]?.body.request_id, first);
});

void test('a failed send keeps its content for the retry', async () => {
  failTurn = true;
  assert.equal(await useStore.getState().send('keep me'), false);
  assert.equal(useStore.getState().pendingSend?.text, 'keep me');
  assert.equal(useStore.getState().pendingSend?.requestId, calls[0]?.body.request_id);
});

void test('the durable overlay excludes delivered entries', async () => {
  const delivered = entry('delivered', 1, 'finished', true);
  const pending = entry('pending', 2, 'accepted');
  durableBody = { ...emptyDurable(), queue: [delivered, pending], pending: [pending] };

  await useStore.getState().refreshDurable();

  const durable = useStore.getState().durable;
  assert.deepEqual(
    durable?.pending.map((item) => item.request_id),
    ['pending'],
  );
  assert.equal(durable?.queue[0]?.delivered, true);
  assert.equal(
    durable?.pending.some((item) => item.delivered),
    false,
  );
});

void test('stopped, interrupted and recovery availability are exposed', async () => {
  durableBody = {
    ...emptyDurable(),
    queue: [entry('stopped', 1, 'stopped'), entry('interrupted', 2, 'interrupted')],
    recoveries: [
      { recovery_session_id: 'gone', available: false },
      { recovery_session_id: 'alive', available: true },
    ],
    conflicted_entries: [entry('conflict', 3, 'finished', true)],
  };

  await useStore.getState().refreshDurable();

  const durable = useStore.getState().durable;
  assert.deepEqual(
    durable?.queue.map((item) => item.state),
    ['stopped', 'interrupted'],
  );
  assert.deepEqual(
    durable?.recoveries.map((item) => item.available),
    [false, true],
  );
  assert.equal(durable?.conflicted_entries[0]?.request_id, 'conflict');
});

void test('a duplicate acceptance does not make the client busy', async () => {
  globalThis.fetch = (input: RequestInfo | URL) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.pathname : '';
    if (path.startsWith('/api/session/transcript')) {
      return Promise.resolve(
        new Response(
          JSON.stringify({ items: [], first_index: 0, has_more: false, total: 0, skills: [], pending: [] }),
          { status: 200 },
        ),
      );
    }
    if (path === '/api/session/durable') {
      return Promise.resolve(new Response(JSON.stringify(emptyDurable()), { status: 200 }));
    }
    return Promise.resolve(
      new Response(JSON.stringify({ accepted: 'turn', request_id: 'id', duplicate: true, state: 'finished' }), {
        status: 200,
      }),
    );
  };
  assert.equal(await useStore.getState().send('again'), true);
  assert.equal(useStore.getState().busy, false);
});

void test('the sections selector separates pending, undelivered and recovery', () => {
  const pending = entry('pending', 1, 'accepted');
  const stopped = entry('stopped', 2, 'stopped');
  const conflict = entry('conflict', 3, 'finished', true);
  const durable = {
    ...emptyDurable(),
    pending: [pending],
    queue: [pending, stopped, conflict],
    conflicted_entries: [conflict],
    recoveries: [
      { recovery_session_id: 'gone', available: false },
      { recovery_session_id: 'alive', available: true },
    ],
  };
  const sections = durableSections(durable, false);
  assert.deepEqual(
    sections.pending.map((item) => item.request_id),
    ['pending'],
  );
  assert.deepEqual(
    sections.undelivered.map((item) => item.request_id),
    ['stopped'],
  );
  assert.deepEqual(
    sections.conflicted.map((item) => item.request_id),
    ['conflict'],
  );
  assert.equal(sections.recoveries[0]?.canOpen, false);
  assert.equal(sections.recoveries[1]?.canOpen, true);
  assert.equal(durableSections(durable, true).recoveries[1]?.canOpen, false);
  assert.equal(
    sections.pending.some((item) => item.request_id === 'conflict'),
    false,
  );
});

void test('a new send marks the client busy', async () => {
  assert.equal(await useStore.getState().send('new turn'), true);
  assert.equal(useStore.getState().busy, true);
});

void test('steer delivery and lagged events refresh the durable read', async () => {
  useStore.getState().applyEvent('steer_delivered', {});
  assert.equal(durableCalls, 1);
  useStore.getState().applyEvent('lagged', {});
  for (let i = 0; i < 5; i++) await Promise.resolve();
  assert.equal(durableCalls, 2);
});

void test('a delivered interrupted entry is only in the recovery section', () => {
  const recovered: DurableEntry = {
    ...entry('recovered', 4, 'interrupted', true),
    destination_id: 'recovery',
  };
  const sections = durableSections({ ...emptyDurable(), queue: [recovered], conflicted_entries: [recovered] }, false);
  assert.equal(
    sections.undelivered.some((item) => item.request_id === 'recovered'),
    false,
  );
  assert.equal(
    sections.conflicted.some((item) => item.request_id === 'recovered'),
    true,
  );
});

void test('a typed 404 from the durable read clears the overlay and reports nothing', async () => {
  // The session has a row for a while: the overlay is painted.
  durableBody = { ...emptyDurable(), pending: [entry('pending', 1, 'accepted')] };
  await useStore.getState().refreshDurable();
  assert.equal(useStore.getState().durable?.pending.length, 1);

  // Then the conversation becomes a draft with no stored row. That is a normal state,
  // not a failure: the old overlay must go, and nothing may be painted about it.
  durableStatus = 404;
  await useStore.getState().refreshDurable();

  assert.equal(useStore.getState().durable, null);
  assert.equal(useStore.getState().notice, '');
  assert.equal(useStore.getState().error, '');
  assert.equal(durableCalls, 2, 'the 404 was read, not skipped');
});

void test('any other failure of the durable read is still reported', async () => {
  durableBody = { ...emptyDurable(), pending: [entry('pending', 1, 'accepted')] };
  await useStore.getState().refreshDurable();
  assert.equal(useStore.getState().durable?.pending.length, 1);

  // A broken server is not "nothing yet": it is worth a sentence, and it must not pass
  // itself off as a draft by quietly clearing what the screen is showing.
  durableStatus = 500;
  durableError = 'the database is locked';
  await useStore.getState().refreshDurable();

  assert.equal(useStore.getState().notice, 'the database is locked');
  assert.equal(useStore.getState().durable?.pending.length, 1);
});
