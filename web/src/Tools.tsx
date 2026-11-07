import { useEffect, useState } from 'react';

import { argumentHint, duration } from './toolPresentation';
import type { ToolView } from './toolPresentation';

/** One layout for streaming calls and paired, persisted history. */
export function Tools({ tools }: { tools: ToolView[] }) {
  const [collapsed, setCollapsed] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const running = tools.some((tool) => tool.phase === 'running');
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  if (tools.length === 0) return null;
  const done = tools.filter((tool) => tool.phase !== 'running').length;
  const failed = tools.filter((tool) => tool.phase === 'failed' || tool.phase === 'interrupted').length;
  const passed = tools.every((tool) => tool.status === 'good' && tool.phase === 'done');
  const badge = running
    ? `${done} / ${tools.length} done`
    : failed
      ? `${failed} failed`
      : passed
        ? 'all passed'
        : 'done';
  const shown = collapsed && !running ? tools.filter((tool) => tool.phase !== 'done') : tools;
  const durations = tools.flatMap((tool) => (tool.ms === undefined ? [] : [tool.ms]));
  const longest = durations.length ? duration(Math.max(...durations)) : '';
  const counts = [...new Set(tools.map((tool) => tool.name))]
    .map((name) => `${tools.filter((tool) => tool.name === name).length} ${name}`)
    .join(' · ');

  return (
    <section className={`tool-batch ${tools.length > 1 ? 'tool-batch-grouped' : ''}`} aria-label="Tool calls">
      {tools.length > 1 ? (
        <button
          type="button"
          className="tool-group-heading"
          onClick={() => setCollapsed(!collapsed)}
          aria-expanded={!collapsed || running}
          disabled={running}
        >
          <span aria-hidden="true">{running ? '⋮' : failed ? '✗' : '✓'}</span>
          <strong>
            {tools.length} tools{running ? '' : ' completed'}
          </strong>
          <span className={`tool-badge ${failed ? 'tool-bad' : ''}`}>{badge}</span>
          <span className="tool-group-time">{longest ? `slowest ${longest}` : ''}</span>
          <span aria-hidden="true">{collapsed && !running ? '›' : '⌄'}</span>
        </button>
      ) : null}
      {collapsed && !running ? <p className="tool-composition">{counts} · collapsed group</p> : null}
      {shown.map((tool) => (
        <ToolRow key={tool.id} tool={tool} now={now} />
      ))}
    </section>
  );
}

function ToolRow({ tool, now }: { tool: ToolView; now: number }) {
  const [expanded, setExpanded] = useState(false);
  const failed = tool.phase === 'failed' || tool.phase === 'interrupted';
  const running = tool.phase === 'running';
  const elapsed = tool.started === undefined ? '' : `${Math.max(0, Math.floor((now - tool.started) / 1000))}s`;
  const summary = running
    ? `running${elapsed ? ` · ${elapsed}` : ''}`
    : (tool.summary ?? (tool.phase === 'interrupted' ? 'interrupted' : failed ? 'failed' : 'done'));
  const args = argumentHint(tool.args);
  const outputId = `tool-output-${tool.id}`;
  return (
    <div className={`tool-entry ${failed ? 'tool-failed' : ''}`}>
      <button
        type="button"
        className="tool-row"
        aria-expanded={expanded}
        aria-controls={outputId}
        onClick={() => setExpanded(!expanded)}
        title={`${tool.name}${args ? ` ${args}` : ''}`}
      >
        <span
          className={`tool-mark ${running ? 'tool-running' : failed ? 'tool-bad' : 'tool-good'}`}
          aria-label={tool.phase}
        >
          {running ? <span className="tool-spinner" aria-hidden="true" /> : failed ? '✗' : '✓'}
        </span>
        <span className="tool-name">{tool.name}</span>
        <span className="tool-arguments">{args}</span>
        <span className={`tool-outcome ${failed ? 'tool-bad' : tool.status === 'good' ? 'tool-good' : ''}`}>
          {summary}
          {!running && tool.ms !== undefined ? ` · ${duration(tool.ms)}` : ''}
        </span>
        <span className="tool-chevron" aria-hidden="true">
          {expanded ? '⌄' : '›'}
        </span>
      </button>
      {failed && !expanded ? (
        <p className="tool-error">↳ {tool.output.split('\n').find((line) => line.trim()) || summary}</p>
      ) : null}
      <div id={outputId} hidden={!expanded}>
        <pre className="tool-output scroll-thin">
          {args ? `${tool.name === 'run' ? '$' : tool.name} ${args}\n` : ''}
          {tool.output || (running ? 'Waiting for output…' : 'No output.')}
        </pre>
      </div>
    </div>
  );
}
