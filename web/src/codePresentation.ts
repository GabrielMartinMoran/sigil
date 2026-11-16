import Prism from 'prismjs';
import 'prismjs/components/prism-python';

export type CodeToken = { text: string; kind: string };

/** Extract source without shortening it; saved calls may encode arguments as JSON. */
export function codeArgument(name: string, value: unknown): string | undefined {
  if (name !== 'execute_code') return undefined;
  if (typeof value === 'string') {
    try {
      return codeArgument(name, JSON.parse(value) as unknown);
    } catch {
      return undefined;
    }
  }
  if (typeof value !== 'object' || value === null) return undefined;
  const code = (value as Record<string, unknown>).code;
  return typeof code === 'string' ? code : undefined;
}

/** Flatten Prism tokens to safe React text nodes; never render generated HTML. */
export function pythonTokens(source: string): CodeToken[] {
  const output: CodeToken[] = [];
  function append(token: string | Prism.Token, inherited = ''): void {
    if (typeof token === 'string') {
      output.push({ text: token, kind: inherited });
      return;
    }
    const content = Array.isArray(token.content) ? token.content : [token.content];
    for (const child of content) append(child, token.type);
  }
  const grammar = Prism.languages.python;
  if (!grammar) return [{ text: source, kind: '' }];
  for (const token of Prism.tokenize(source, grammar)) append(token);
  return output;
}
