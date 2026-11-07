import { useDeferredValue, useEffect, useState } from 'react';

import { MarkdownText } from './Markdown';
import { Tools } from './Tools';
import { useStore } from './store';

/**
 * The live turn: the text as it arrives, the tools as they run.
 *
 * It is separate from `Transcript` because it is the only part that changes on every
 * token, and re-rendering a long conversation sixty times a second to append a
 * character is how a chat client gets hot. When the turn ends this component is gone
 * — the transcript was refetched and holds the same words, written once.
 */
export function Streaming() {
  const busy = useStore((store) => store.busy);
  // The mount of this component is the start of the turn: the parent only renders it
  // while something is running, so the clock resets by unmounting, not by an effect.
  if (!busy) return null;
  return <LiveTurn />;
}

function LiveTurn() {
  const stream = useStore((store) => store.stream);
  const thinking = useStore((store) => store.state?.model.thinking ?? 'default');
  const [elapsed, setElapsed] = useState(0);
  // Parsing Markdown on every delta is wasted work: the document changes by a few
  // characters and the parser walks all of it. `useDeferredValue` lets the tokens
  // land immediately and renders the parsed version whenever the browser is idle,
  // which is the same trade the terminal makes with its 80 ms re-render interval.
  const text = useDeferredValue(stream.text);

  useEffect(() => {
    const timer = window.setInterval(() => setElapsed((seconds) => seconds + 1), 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="space-y-2">
      {[...new Set(stream.tools.map((tool) => tool.batch))].map((batch) => (
        <Tools key={batch ?? 0} tools={stream.tools.filter((tool) => tool.batch === batch)} />
      ))}

      {stream.thinking ? (
        <details className="text-ink-faint text-xs">
          <summary className="cursor-pointer font-mono select-none">thinking · {elapsed}s</summary>
          <pre className="scroll-thin border-border-subtle mt-2 max-h-52 overflow-auto border-l pl-3 font-mono whitespace-pre-wrap">
            {stream.thinking}
          </pre>
        </details>
      ) : null}

      {stream.text ? (
        <div className="text-sm leading-relaxed">
          <MarkdownText text={text} />
          <span className="bg-accent ml-0.5 inline-block h-4 w-[2px] animate-pulse align-text-bottom" />
        </div>
      ) : (
        <div className="text-ink-faint font-mono text-xs">
          {thinking !== 'default' ? `working · ${thinking} · ${elapsed}s` : `working · ${elapsed}s`}
        </div>
      )}
    </div>
  );
}
