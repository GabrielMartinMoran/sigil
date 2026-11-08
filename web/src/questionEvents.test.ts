import assert from 'node:assert/strict';
import { test } from 'node:test';

import { useStore } from './store';

void test('question open and close events refresh the authoritative panel state', () => {
  const original = useStore.getState().refresh;
  let refreshed = 0;
  useStore.setState({
    refresh: () => {
      refreshed++;
      return Promise.resolve();
    },
  });
  try {
    useStore.getState().applyEvent('question', { id: 'q1' });
    useStore.getState().applyEvent('question_closed', { id: 'q1' });
    assert.equal(refreshed, 2);
  } finally {
    useStore.setState({ refresh: original });
  }
});
