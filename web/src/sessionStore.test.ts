import assert from 'node:assert/strict';
import test from 'node:test';

import { api } from './api';
import { useStore } from './store';
import type { State } from './types';

void test('a stale state read cannot overwrite a newer selection', async () => {
  const original = api.state;
  let complete!: (state: State) => void;
  api.state = () =>
    new Promise<State>((resolve) => {
      complete = resolve;
    });
  try {
    useStore.setState({ selectedSessionId: 'old', selectionGeneration: 1, state: null });
    const pending = useStore.getState().refresh();
    useStore.setState({ selectedSessionId: 'new', selectionGeneration: 2 });
    complete({ session: { id: 'old' }, busy: true } as State);
    await pending;
    assert.equal(useStore.getState().state, null);
    assert.equal(useStore.getState().selectedSessionId, 'new');
  } finally {
    api.state = original;
  }
});

void test('reconnecting reopens the selected persisted session instead of bootstrapping another', async () => {
  const originalState = api.state;
  const originalOpen = api.openSession;
  const { ApiError } = await import('./api');
  let reads = 0;
  const opened: string[] = [];
  api.state = (id) => {
    assert.equal(id, 'saved');
    if (reads++ === 0) return Promise.reject(new ApiError('Session is not resident', 404));
    return Promise.resolve({ session: { id: 'saved' }, busy: false } as unknown as State);
  };
  api.openSession = (id) => {
    opened.push(id);
    return Promise.resolve({ session: { id } } as Awaited<ReturnType<typeof api.openSession>>);
  };
  useStore.setState({ selectedSessionId: 'saved', selectionGeneration: 12, state: null });
  try {
    await useStore.getState().refresh();
    assert.deepEqual(opened, ['saved']);
    assert.equal(useStore.getState().state?.session.id, 'saved');
  } finally {
    api.state = originalState;
    api.openSession = originalOpen;
  }
});
