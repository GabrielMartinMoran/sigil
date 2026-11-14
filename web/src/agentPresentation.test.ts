import assert from 'node:assert/strict';
import { test } from 'node:test';

import { agentOptions } from './agentPresentation';
import type { AgentView } from './types';

function agent(name: string, role = 'agent'): AgentView {
  return {
    name,
    description: 'Reviews code.',
    role,
    source: 'project',
    max_steps: null,
    active: false,
    permissions: 'only read',
  };
}

void test('web options identify selectable profiles without exposing permissions', () => {
  assert.deepEqual(agentOptions([agent('reviewer'), agent('worker', 'subagent')]), [
    { name: 'reviewer', label: 'Reviewer', description: 'Reviews code.', origin: 'project' },
  ]);
});

void test('an empty catalog offers no profiles', () => {
  assert.deepEqual(agentOptions([]), []);
});
