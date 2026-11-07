import { useCallback, useState } from 'react';

import { Composer } from './Composer';
import { Login } from './Login';
import { QuestionPanel } from './QuestionPanel';
import { Sidebar } from './Sidebar';
import { Streaming } from './Streaming';
import { Transcript } from './Transcript';
import { useStore } from './store';
import { useEvents } from './useEvents';

/**
 * The shell.
 *
 * It is a router with one route, because the client has one screen: the conversation.
 * The panel is state, not a route — a drawer opened on a phone is not a place you can
 * come back to, and a URL for it would be a URL that lies.
 */
export function App() {
  const phase = useStore((store) => store.phase);
  const status = useStore((store) => store.status);
  const error = useStore((store) => store.error);
  const notice = useStore((store) => store.notice);
  const state = useStore((store) => store.state);
  const items = useStore((store) => store.items);
  const question = useStore((store) => store.state?.question ?? null);
  const boot = useStore((store) => store.boot);

  const [panel, setPanel] = useState(false);

  useEvents(phase === 'ready');

  // `boot` runs once: it decides between the login and the app, and re-running it on
  // every render would fight the login it is trying to fall back to.
  const [started, setStarted] = useState(false);
  if (!started) {
    setStarted(true);
    void boot();
  }

  const closePanel = useCallback(() => setPanel(false), []);

  if (phase === 'loading') {
    return (
      <main className="flex min-h-full items-center justify-center">
        <p className="text-ink-faint font-mono text-sm">loading…</p>
      </main>
    );
  }

  if (phase === 'login') return <Login status={status} />;

  if (phase === 'error') {
    return (
      <main className="flex min-h-full items-center justify-center p-6">
        <div className="max-w-md text-center">
          <p className="text-danger font-mono text-sm">{error}</p>
          <button
            type="button"
            onClick={() => {
              useStore.setState({ phase: 'loading' });
              void boot();
            }}
            className="border-border-subtle text-ink-muted mt-4 rounded border px-3 py-1.5 text-sm"
          >
            Retry
          </button>
        </div>
      </main>
    );
  }

  return (
    <div className="flex h-full">
      <Sidebar open={panel} onClose={closePanel} />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="border-border-subtle flex items-center gap-2 border-b px-3 py-2 sm:px-4">
          <button
            type="button"
            onClick={() => setPanel(true)}
            aria-label="Open the panel"
            className="text-ink-muted hover:text-ink rounded px-1.5 py-1 md:hidden"
          >
            ☰
          </button>
          <h1 className="text-ink min-w-0 flex-1 truncate text-sm font-medium">
            {state?.session.display_title ?? 'sigil'}
          </h1>
          {state?.busy ? (
            <span className="text-accent shrink-0 animate-pulse font-mono text-[11px]">working</span>
          ) : null}
        </header>

        {notice ? (
          <div className="border-border-subtle bg-surface-raised text-warn border-b px-3 py-1.5 font-mono text-[11px] sm:px-4">
            {notice}
          </div>
        ) : null}
        {error && phase === 'ready' ? (
          <div className="border-border-subtle bg-surface-raised text-danger border-b px-3 py-1.5 font-mono text-[11px] sm:px-4">
            {error}
          </div>
        ) : null}

        <Transcript items={items} />

        <div className="mx-auto w-full max-w-3xl px-3 pb-2 sm:px-4">
          <Streaming />
        </div>

        {question !== null ? <QuestionPanel question={question} /> : <Composer />}
      </div>
    </div>
  );
}
