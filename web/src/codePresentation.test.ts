import assert from 'node:assert/strict';
import { test } from 'node:test';

import { codeArgument, pythonTokens } from './codePresentation';

void test('code argument preserves complete source from live and serialized history', () => {
  const source = 'async def main():\n    return "hello"\n';
  assert.equal(codeArgument('execute_code', { code: source }), source);
  assert.equal(codeArgument('execute_code', JSON.stringify({ code: source })), source);
  assert.equal(codeArgument('shell', { code: source }), undefined);
  assert.equal(codeArgument('execute_code', '{invalid'), undefined);
});

void test('tokenization preserves source and distinguishes Python syntax', () => {
  const source = 'async def main():\n    return "<script>"\n';
  const tokens = pythonTokens(source);
  assert.equal(tokens.map((token) => token.text).join(''), source);
  assert.ok(tokens.some((token) => token.kind === 'keyword'));
  assert.ok(tokens.some((token) => token.kind === 'string'));
});
