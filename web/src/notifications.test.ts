import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import {
  attentionMessage,
  closeAttention,
  createAttentionListener,
  notificationState,
  setNotificationsEnabled,
  showAttention,
  subscribeNotifications,
} from './notifications';

function browser(permission: NotificationPermission = 'granted', enabled = true) {
  const sent: { title: string; options?: NotificationOptions; notice: FakeNotification }[] = [];
  const storage = new Map<string, string>([['sigil.desktop-notifications', String(enabled)]]);
  class FakeNotification {
    static permission = permission;
    static requests = 0;
    static requestPermission() {
      this.requests++;
      return Promise.resolve(this.permission);
    }
    constructor(title: string, options?: NotificationOptions) {
      sent.push({ title, options, notice: this });
    }
    onclick: (() => void) | null = null;
    closed = false;
    close() {
      this.closed = true;
    }
  }
  const target = Object.assign(new EventTarget(), {
    isSecureContext: true,
    localStorage: {
      getItem: (key: string) => storage.get(key),
      setItem: (key: string, value: string) => storage.set(key, value),
    },
    focus() {},
  });
  Object.defineProperty(globalThis, 'Notification', { configurable: true, value: FakeNotification });
  Object.defineProperty(globalThis, 'window', { configurable: true, value: target });
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: {} });
  return { sent, FakeNotification, target };
}

afterEach(closeAttention);

void test('attention messages contain no server-provided private text', () => {
  assert.equal(attentionMessage('question', { questions: ['secret'] }), 'A question is waiting for your answer.');
  assert.equal(
    attentionMessage('turn_finished', { status: 'ok', text: 'secret' }),
    'Turn finished. Your response is ready.',
  );
  assert.equal(
    attentionMessage('turn_finished', { status: 'error', error: 'secret' }),
    'Turn failed. Open Sigil for details.',
  );
  assert.equal(attentionMessage('turn_finished', { status: 'cancelled' }), null);
  assert.equal(attentionMessage('tool_finished', {}), null);
});

void test('desktop delivery requires permission and opt-in, without prompting on an event', () => {
  for (const permission of ['default', 'denied'] as const) {
    const { sent, FakeNotification } = browser(permission);
    showAttention('question', {});
    assert.equal(sent.length, 0);
    assert.equal(FakeNotification.requests, 0);
  }
  const { sent } = browser('granted', false);
  showAttention('question', {});
  assert.equal(sent.length, 0);
});

void test('notifications can be enabled and disabled explicitly', async () => {
  const { FakeNotification } = browser('granted', false);
  assert.equal(await setNotificationsEnabled(true), true);
  showAttention('question', {});
  assert.equal(await setNotificationsEnabled(false), false);
  assert.equal(FakeNotification.requests, 0);
});

void test('replayed events and duplicate sequence numbers do not notify again', () => {
  const { sent } = browser();
  const listen = createAttentionListener(10);
  listen('turn_finished', { seq: 9, status: 'ok' });
  listen('question', { seq: 11, id: 'q1' });
  listen('question', { seq: 11, id: 'q1' });
  listen('turn_finished', { seq: 12, status: 'cancelled' });
  listen('turn_finished', { seq: 13, status: 'error' });
  assert.equal(sent.length, 2);
  assert.equal(sent[0]?.options?.tag, 'sigil-attention');
});

void test('invalid sequence values cannot poison subsequent attention events', () => {
  const { sent } = browser();
  const listen = createAttentionListener(10);
  for (const seq of [NaN, Infinity, -1, 10.5, '11', undefined]) listen('question', { seq });
  listen('question', { seq: 11 });
  assert.equal(sent.length, 1);
});

void test('closing a question, starting a turn or cancelling dismisses obsolete alerts', () => {
  for (const type of ['question_closed', 'turn_started', 'turn_finished']) {
    const { sent } = browser();
    showAttention('question', {});
    showAttention(type, { status: 'cancelled' });
    assert.equal(sent[0]?.notice.closed, true);
    assert.equal(sent.length, 1);
  }
});

void test('new attention replaces the old notice and disabling dismisses it', async () => {
  const { sent } = browser();
  showAttention('question', {});
  showAttention('turn_finished', { status: 'ok' });
  assert.equal(sent[0]?.notice.closed, true);
  assert.equal(sent[1]?.notice.closed, false);
  await setNotificationsEnabled(false);
  assert.equal(sent[1]?.notice.closed, true);
});

void test('permission requests occur only on explicit enable and notify settings subscribers', async () => {
  const { FakeNotification } = browser('default', false);
  FakeNotification.permission = 'granted';
  let updates = 0;
  const unsubscribe = subscribeNotifications(() => updates++);
  assert.equal(await setNotificationsEnabled(true), true);
  assert.equal(notificationState(), 'enabled');
  assert.equal(updates, 1);
  unsubscribe();
  await setNotificationsEnabled(false);
  assert.equal(updates, 1);
});

void test('returning to the app or changing settings in another tab refreshes permissions', () => {
  const { target, FakeNotification, sent } = browser();
  let updates = 0;
  const unsubscribe = subscribeNotifications(() => updates++);
  showAttention('question', {});
  FakeNotification.permission = 'denied';
  target.dispatchEvent(new Event('focus'));
  assert.equal(notificationState(), 'blocked');
  assert.equal(sent[0]?.notice.closed, true);
  target.dispatchEvent(new Event('storage'));
  assert.equal(updates, 2);
  unsubscribe();
  target.dispatchEvent(new Event('focus'));
  assert.equal(updates, 2);
});

void test('blocked storage, rejected permission requests and insecure contexts fail safely', async () => {
  const { target, FakeNotification } = browser('default', false);
  FakeNotification.requestPermission = () => Promise.reject(new Error('blocked'));
  assert.equal(await setNotificationsEnabled(true), false);
  assert.equal(FakeNotification.requests, 0);
  target.isSecureContext = false;
  assert.equal(notificationState(), 'unsupported');
  assert.equal(await setNotificationsEnabled(true), false);
  target.isSecureContext = true;
  Object.defineProperty(target, 'localStorage', {
    get() {
      throw new Error('storage blocked');
    },
  });
  assert.equal(notificationState(), 'disabled');
  assert.equal(await setNotificationsEnabled(true), false);
});

void test('unsupported browsers and rejected notifications do not throw', async () => {
  browser();
  Reflect.deleteProperty(globalThis, 'Notification');
  assert.doesNotThrow(() => showAttention('question', {}));
  assert.equal(await setNotificationsEnabled(true), false);
  browser();
  class Rejected {
    static permission = 'granted';
    constructor() {
      throw new Error('blocked');
    }
  }
  Object.defineProperty(globalThis, 'Notification', { configurable: true, value: Rejected });
  assert.doesNotThrow(() => showAttention('question', {}));
});

void test('explicit enable requests permission once and saves the granted result', async () => {
  const { FakeNotification } = browser('default', false);
  FakeNotification.requestPermission = () => {
    FakeNotification.requests++;
    FakeNotification.permission = 'granted';
    return Promise.resolve('granted');
  };
  assert.equal(await setNotificationsEnabled(true), true);
  assert.equal(FakeNotification.requests, 1);
  assert.equal(notificationState(), 'enabled');
});

void test('permission change listeners update live and are removed on cleanup', async () => {
  const { FakeNotification } = browser();
  const permission = new EventTarget();
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { permissions: { query: () => Promise.resolve(permission) } },
  });
  let updates = 0;
  const unsubscribe = subscribeNotifications(() => updates++);
  await Promise.resolve();
  FakeNotification.permission = 'denied';
  permission.dispatchEvent(new Event('change'));
  assert.equal(notificationState(), 'blocked');
  assert.equal(updates, 2);
  unsubscribe();
  permission.dispatchEvent(new Event('change'));
  assert.equal(updates, 2);
});

void test('unmounting before permission query resolves does not leak listeners', async () => {
  browser();
  const permission = new EventTarget();
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { permissions: { query: () => Promise.resolve(permission) } },
  });
  let updates = 0;
  const unsubscribe = subscribeNotifications(() => updates++);
  unsubscribe();
  await Promise.resolve();
  permission.dispatchEvent(new Event('change'));
  assert.equal(updates, 0);
});

void test('notification click focuses the app and closes its notice', () => {
  const { target, sent } = browser();
  let focused = false;
  target.focus = () => {
    focused = true;
  };
  showAttention('question', {});
  sent[0]?.notice.onclick?.();
  assert.equal(focused, true);
  assert.equal(sent[0]?.notice.closed, true);
});

void test('a desktop close failure cannot block subsequent alerts', () => {
  const { sent } = browser();
  showAttention('question', {});
  const first = sent[0]?.notice;
  assert.ok(first);
  first.close = () => {
    throw new Error('desktop unavailable');
  };
  assert.doesNotThrow(() => showAttention('turn_finished', { status: 'ok' }));
  assert.equal(sent.length, 2);
});

void test('notification identifies project and conversation without including event content', () => {
  const { sent } = browser();
  const listen = createAttentionListener(0, () => ({ project: 'sigil:feature', conversation: 'Improve alerts' }));
  listen('question', { seq: 1, questions: ['secret'] });
  assert.equal(sent[0]?.title, 'sigil:feature — Improve alerts');
  assert.equal(sent[0]?.options?.body, 'A question is waiting for your answer.');
});

void test('notification uses current context on each event and tolerates missing titles', () => {
  const { sent } = browser();
  let context = { project: 'one', conversation: 'First' };
  const listen = createAttentionListener(0, () => context);
  listen('turn_finished', { seq: 1, status: 'ok' });
  context = { project: 'two', conversation: '' };
  listen('turn_finished', { seq: 2, status: 'error' });
  assert.equal(sent[0]?.title, 'one — First');
  assert.equal(sent[1]?.title, 'two');
});
