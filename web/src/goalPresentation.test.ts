import assert from 'node:assert/strict';
import { test } from 'node:test';

import { presentGoal } from './goalPresentation';
import type { GoalView } from './types';

function goal(status: string, yolo = false): GoalView {
  return { text: 'Ship the CSV export', yolo, status };
}

void test('a completed goal is marked and toned apart from a running one', () => {
  assert.deepEqual(presentGoal(goal('completed')), {
    mark: '✓',
    tone: 'text-accent',
    text: 'Ship the CSV export',
    mode: null,
  });
  assert.equal(presentGoal(goal('active')).mark, '▸');
  assert.equal(presentGoal(goal('blocked')).tone, 'text-warn');
});

void test('a yolo goal says so, and a plain goal does not', () => {
  assert.equal(presentGoal(goal('active', true)).mode, 'yolo');
  assert.equal(presentGoal(goal('active')).mode, null);
});

void test('an unknown status still paints a line instead of failing', () => {
  assert.deepEqual(presentGoal(goal('invented')), {
    mark: '·',
    tone: 'text-ink-muted',
    text: 'Ship the CSV export',
    mode: null,
  });
});
