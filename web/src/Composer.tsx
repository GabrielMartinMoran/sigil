import { useEffect, useRef, useState, type KeyboardEvent } from 'react';

import { useStore } from './store';

/** Height the composer grows to before it scrolls instead of pushing the page. */
const MAX_ROWS = 10;
const LINE_HEIGHT = 22;

/**
 * The composer.
 *
 * `Enter` sends and `Shift+Enter` breaks the line, which is the convention every
 * chat has. While a turn runs the same box steers it: the server decides which of
 * the two happened, and the placeholder says so rather than the text being refused.
 */
export function Composer() {
  const send = useStore((store) => store.send);
  const abort = useStore((store) => store.abort);
  const busy = useStore((store) => store.busy);
  const [text, setText] = useState('');
  const area = useRef<HTMLTextAreaElement>(null);
  const lastSubmission = useRef('');

  useEffect(() => {
    const node = area.current;
    if (node === null) return;
    node.style.height = 'auto';
    node.style.height = `${Math.min(node.scrollHeight, MAX_ROWS * LINE_HEIGHT)}px`;
  }, [text]);

  const submit = () => {
    const value = text.trim();
    if (!value) return;
    lastSubmission.current = value;
    void send(value).then((accepted) => {
      if (accepted) setText((current) => (current.trim() === value ? '' : current));
    });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (event.key === 'ArrowUp' && text === '' && lastSubmission.current) {
      event.preventDefault();
      setText(lastSubmission.current);
      return;
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
      return;
    }
    if (event.key === 'Escape' && busy) {
      event.preventDefault();
      void abort();
    }
  };

  return (
    <div className="border-border-subtle bg-surface border-t px-3 py-3 sm:px-4">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <textarea
          ref={area}
          rows={1}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder={busy ? 'Steer the running turn…' : 'Ask something…'}
          className="scroll-thin border-border-subtle bg-surface-sunken text-ink placeholder:text-ink-faint focus:border-accent max-h-[220px] min-h-[42px] flex-1 resize-none rounded-lg border px-3 py-2.5 text-sm leading-[22px] outline-none"
        />
        {busy ? (
          <button
            type="button"
            onClick={() => void abort()}
            className="border-danger/50 text-danger h-[42px] shrink-0 rounded-lg border px-3 text-sm font-medium"
          >
            Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!text.trim()}
            className="bg-accent text-accent-ink h-[42px] shrink-0 rounded-lg px-4 text-sm font-semibold disabled:opacity-40"
          >
            Send
          </button>
        )}
      </div>
      <p className="text-ink-faint mx-auto mt-1.5 max-w-3xl font-mono text-[11px]">
        Enter sends · Shift+Enter breaks the line{busy ? ' · Esc stops the turn' : ''}
      </p>
    </div>
  );
}
