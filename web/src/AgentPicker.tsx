import { useId, useRef } from 'react';

import { agentLabel, agentOptions } from './agentPresentation';
import { useStore } from './store';

export function AgentPicker() {
  const state = useStore((store) => store.state);
  const busy = useStore((store) => store.busy);
  const pending = useStore((store) => store.selectingAgent);
  const selectAgent = useStore((store) => store.selectAgent);
  const details = useRef<HTMLDetailsElement>(null);
  const summary = useRef<HTMLElement>(null);
  const id = useId();
  const active = state?.agent?.name ?? '';
  const options = agentOptions(state?.agents ?? []);

  async function choose(name: string): Promise<void> {
    if (await selectAgent(name)) {
      if (details.current) details.current.open = false;
      summary.current?.focus();
    }
  }

  return (
    <details
      ref={details}
      className="relative min-w-0 shrink-0"
      onKeyDown={(event) => {
        if (event.key === 'Escape' && details.current?.open) {
          details.current.open = false;
          summary.current?.focus();
          event.stopPropagation();
        }
      }}
    >
      <summary
        ref={summary}
        aria-label={`Agent: ${active ? agentLabel(active) : 'No profile'}. Change agent`}
        aria-controls={id}
        className="border-border-subtle text-ink-muted focus-visible:outline-accent max-w-28 cursor-pointer truncate rounded border px-2 py-1 text-xs focus-visible:outline-2 sm:max-w-48"
        title={active ? agentLabel(active) : 'No profile'}
      >
        {active ? agentLabel(active) : 'No profile'}
      </summary>
      <div
        id={id}
        aria-label="Agent profiles"
        aria-busy={pending}
        className="border-border-subtle bg-surface-raised fixed top-12 right-3 z-30 mt-2 max-h-80 w-72 max-w-[calc(100vw-2rem)] overflow-y-auto rounded border p-2 shadow-lg sm:absolute sm:top-auto sm:right-0"
      >
        <p className="text-ink-muted px-2 py-1 text-xs">Choose an agent</p>
        {busy ? (
          <p className="text-ink-muted px-2 py-1 text-xs">Wait for the turn to finish to change agents.</p>
        ) : null}
        {options.length === 0 ? (
          <p className="text-ink-muted px-2 py-2 text-xs">No selectable profiles installed.</p>
        ) : null}
        {options.map((option) => (
          <button
            key={option.name}
            type="button"
            aria-pressed={option.name === active}
            disabled={busy || pending}
            onClick={() => {
              void choose(option.name);
            }}
            className="text-ink hover:bg-surface-sunken focus-visible:outline-accent block w-full rounded px-2 py-2 text-left text-xs focus-visible:outline-2 disabled:opacity-40"
          >
            <span className="block font-medium">
              {option.label}
              {option.name === active ? ' (current)' : ''}
            </span>
            <span className="text-ink-faint block text-[10px]">
              {option.name} · {option.origin}
            </span>
            <span className="text-ink-muted mt-1 block">{option.description}</span>
          </button>
        ))}
      </div>
    </details>
  );
}
