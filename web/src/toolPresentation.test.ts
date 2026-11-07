import assert from 'node:assert/strict';
import { test } from 'node:test';

import { argumentHint, finishTool, transcriptBlocks } from './toolPresentation';

void test('pair results by call identity even when the same tool finishes out of order', () => {
  const blocks = transcriptBlocks([
    { kind: 'tool_call', index: 1, call_id: 'a', tool_name: 'run', args: { cmds: ['first'] } },
    { kind: 'tool_call', index: 1, call_id: 'b', tool_name: 'run', args: { cmds: ['second'] } },
    { kind: 'tool_result', index: 2, call_id: 'b', tool_name: 'run', text: 'second result', ok: false },
    { kind: 'tool_result', index: 2, call_id: 'a', tool_name: 'run', text: 'first result', ok: true },
    { kind: 'assistant', index: 3, text: 'done' },
  ]);
  assert.equal(blocks.length, 2);
  const group = blocks[0];
  assert.equal(group?.kind, 'tools');
  if (group?.kind !== 'tools') throw new Error('expected tools');
  assert.equal(group.tools[0]?.output, 'first result');
  assert.equal(group.tools[1]?.phase, 'failed');
});

void test('a missing result is interrupted history, not a live spinner forever', () => {
  const block = transcriptBlocks([{ kind: 'tool_call', index: 0, call_id: 'a', tool_name: 'run' }])[0];
  assert.equal(block?.kind, 'tools');
  if (block?.kind !== 'tools') throw new Error('expected tools');
  assert.equal(block.tools[0]?.phase, 'interrupted');
});

void test('compact arguments prioritize commands and paths, never file contents', () => {
  assert.equal(argumentHint({ cmds: ['pytest -q'] }), 'pytest -q');
  assert.equal(argumentHint({ path: 'tests/a.py', content: 'long content' }), 'tests/a.py');
  assert.equal(argumentHint('{"path":"a.py"}'), 'a.py');
});

void test('one tool finish cannot mark every same-name invocation done', () => {
  const tools = [0, 1].map((id) => ({ id: String(id), name: 'run', phase: 'running' as const, args: {}, output: '' }));
  const finished = finishTool(tools, { tool_name: 'run', ok: true, summary: { text: '1 failed', status: 'bad' } });
  assert.equal(finished[0]?.phase, 'failed');
  assert.equal(finished[0]?.summary, '1 failed');
  assert.equal(finished[1]?.phase, 'running');
});

void test('live invocation IDs target the correct row when parallel calls finish out of order', () => {
  const tools = [0, 1].map((id) => ({ id: String(id), name: 'run', phase: 'running' as const, args: {}, output: '' }));
  const finished = finishTool(tools, { invocation_id: '1', tool_name: 'run', ok: true, preview: 'second result' });
  assert.equal(finished[0]?.phase, 'running');
  assert.equal(finished[1]?.output, 'second result');
  assert.equal(finished[1]?.phase, 'done');
});

void test('historical human summaries preserve failed outcomes without leaking envelopes', () => {
  const block = transcriptBlocks([
    { kind: 'tool_call', index: 0, call_id: 'a', tool_name: 'run', args: { cmds: ['pytest'] } },
    {
      kind: 'tool_result',
      index: 1,
      call_id: 'a',
      tool_name: 'run',
      text: 'NameError',
      ok: true,
      summary: { text: '1 failed', status: 'bad' },
    },
  ])[0];
  if (block?.kind !== 'tools') throw new Error('expected tools');
  assert.equal(block.tools[0]?.phase, 'failed');
  assert.equal(block.tools[0]?.summary, '1 failed');
  assert.equal(block.tools[0]?.output, 'NameError');
});
