import type { TranscriptItem } from './types';

export type ToolView = {
  id: string;
  name: string;
  phase: 'running' | 'done' | 'failed' | 'interrupted';
  args: unknown;
  output: string;
  summary?: string;
  status?: 'good' | 'bad' | 'neutral';
  ms?: number;
  started?: number;
  batch?: number;
};

export type TranscriptBlock = { kind: 'item'; item: TranscriptItem } | { kind: 'tools'; id: string; tools: ToolView[] };

function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {};
}

export function argumentHint(value: unknown): string {
  if (typeof value === 'string') {
    try {
      return argumentHint(JSON.parse(value) as unknown);
    } catch {
      return value.replace(/\s+/g, ' ').slice(0, 160);
    }
  }
  const args = record(value);
  for (const key of ['cmds', 'command', 'path', 'query', 'url']) {
    const arg = args[key];
    if (Array.isArray(arg)) return arg.filter((item): item is string => typeof item === 'string').join(' ; ');
    if (typeof arg === 'string' && arg) return arg.replace(/\s+/g, ' ');
  }
  return Object.entries(args)
    .filter(([, arg]) => typeof arg === 'string' || typeof arg === 'number')
    .slice(0, 2)
    .map(([key, arg]) => `${key}=${String(arg).slice(0, 80)}`)
    .join(', ');
}

export function finishTool(tools: ToolView[], data: Record<string, unknown>): ToolView[] {
  const index = tools.findIndex((tool) =>
    data.invocation_id ? tool.id === data.invocation_id : tool.name === data.tool_name && tool.phase === 'running',
  );
  const summary = record(data.summary);
  return tools.map((tool, position) => {
    if (position !== index) return tool;
    const status = summary.status === 'good' || summary.status === 'bad' ? summary.status : 'neutral';
    return {
      ...tool,
      phase: data.ok && status !== 'bad' ? 'done' : 'failed',
      output:
        typeof data.error === 'string' && data.error
          ? data.error
          : typeof data.preview === 'string'
            ? data.preview
            : '',
      summary: typeof summary.text === 'string' ? summary.text : undefined,
      status,
      ms: typeof data.duration_ms === 'number' ? data.duration_ms : undefined,
    };
  });
}

/** Results are paired by ID across message boundaries; missing results stay visible. */
export function transcriptBlocks(items: TranscriptItem[]): TranscriptBlock[] {
  const results = new Map(
    items.filter((item) => item.kind === 'tool_result' && item.call_id).map((item) => [item.call_id, item]),
  );
  const paired = new Set(items.filter((item) => item.kind === 'tool_call' && item.call_id).map((item) => item.call_id));
  const blocks: TranscriptBlock[] = [];
  let group: Extract<TranscriptBlock, { kind: 'tools' }> | undefined;
  let batch = -1;
  for (const [position, item] of items.entries()) {
    if (item.kind === 'tool_result' && paired.has(item.call_id)) continue;
    if (item.kind !== 'tool_call' && item.kind !== 'tool_result') {
      blocks.push({ kind: 'item', item });
      group = undefined;
      continue;
    }
    if (!group || batch !== item.index) {
      group = { kind: 'tools', id: `batch-${item.index}-${position}`, tools: [] };
      blocks.push(group);
      batch = item.index;
    }
    const result = item.kind === 'tool_result' ? item : results.get(item.call_id);
    group.tools.push({
      id: item.call_id || `call-${position}`,
      name: item.tool_name ?? 'tool',
      args: item.args,
      phase: !result ? 'interrupted' : result.ok && result.summary?.status !== 'bad' ? 'done' : 'failed',
      summary: result?.summary?.text,
      status: result?.summary?.status,
      output: result?.text ?? 'Interrupted before a result was received.',
    });
  }
  return blocks;
}

export function duration(ms: number | undefined): string {
  if (ms === undefined) return '';
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}
