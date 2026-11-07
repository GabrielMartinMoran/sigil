import { useEffect, useRef } from 'react';

import { MarkdownText } from './Markdown';
import type { Attachment, TranscriptItem } from './types';

/**
 * The conversation, as a list of items.
 *
 * Three decisions:
 *
 * - **Items, not messages.** A tool call and its result travel as two of them with
 *   the same `call_id`; pairing them here would lose the case where the result never
 *   arrived, which is exactly what a cancelled turn looks like.
 * - **The tool result is text; the answer is Markdown.** A tool result is what the
 *   tool produced and is shown verbatim. The answer is prose the model wrote on
 *   purpose, and rendering it is most of the value of a chat UI — the terminal client
 *   does the same with Rich `Markdown`. It goes through `react-markdown`, which builds
 *   elements and never injects HTML, so a model cannot run anything here.
 * - **Attachments are described, never inlined.** The server sends an image as its
 *   pixels and its size, and the `[Image #n]` marker in the text is what ties it to a
 *   sentence. That is exactly what the terminal prints next to a pasted picture.
 *
 * Autoscroll only when the reader was at the bottom: scrolling up during a long turn
 * must not be undone by the next token.
 */
export function Transcript({ items }: { items: TranscriptItem[] }) {
  const container = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const node = container.current;
    if (node === null) return;
    const onScroll = () => {
      const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
      stick.current = distance < 80;
    };
    node.addEventListener('scroll', onScroll, { passive: true });
    return () => node.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    const node = container.current;
    if (node !== null && stick.current) node.scrollTop = node.scrollHeight;
  }, [items]);

  return (
    <div ref={container} className="scroll-thin flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-4 px-3 py-5 sm:px-4">
        {items.length === 0 ? (
          <p className="text-ink-faint py-16 text-center text-sm">Nothing here yet. Ask something and it starts.</p>
        ) : null}
        {items.map((item, position) => (
          <Bubble key={`${item.index}-${item.call_id ?? ''}-${position}`} item={item} />
        ))}
      </div>
    </div>
  );
}

function Bubble({ item }: { item: TranscriptItem }) {
  if (item.kind === 'user') {
    return (
      <div className="flex justify-end">
        <div className="bg-surface-raised text-ink max-w-[85%] rounded-2xl rounded-br-sm px-4 py-2.5 text-sm">
          {item.text ? <p className="whitespace-pre-wrap">{item.text}</p> : null}
          {item.attachments?.length ? (
            <div className="mt-1.5 flex flex-wrap justify-end gap-1.5">
              {item.attachments.map((attachment, index) => (
                <AttachmentChip key={attachment.identifier ?? index} attachment={attachment} />
              ))}
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  if (item.kind === 'thinking') {
    return (
      <details className="group text-ink-faint text-xs">
        <summary className="hover:text-ink-muted cursor-pointer font-mono select-none">thinking</summary>
        <pre className="scroll-thin border-border-subtle mt-2 max-h-60 overflow-auto border-l pl-3 font-mono whitespace-pre-wrap">
          {item.text}
        </pre>
      </details>
    );
  }

  if (item.kind === 'tool_call') {
    return (
      <div className="text-ink-faint font-mono text-xs">
        <span className="text-accent">▸ {item.tool_name}</span>
        {item.args !== undefined && item.args !== null ? (
          <span className="text-ink-faint/70 ml-2 break-all">{summarize(item.args)}</span>
        ) : null}
      </div>
    );
  }

  if (item.kind === 'tool_result') {
    return (
      <details className="group font-mono text-xs">
        <summary className={`cursor-pointer select-none ${item.ok ? 'text-ink-faint' : 'text-danger'}`}>
          {item.ok ? '✓' : '✗'} {item.tool_name}
        </summary>
        <pre className="scroll-thin border-border-subtle bg-surface-sunken text-ink-muted mt-1.5 max-h-72 overflow-auto rounded border p-2.5 whitespace-pre-wrap">
          {item.text}
        </pre>
      </details>
    );
  }

  return <MarkdownText text={item.text ?? ''} />;
}

/** One attachment, as the terminal describes it: `640×480 PNG · 118 KB`. */
function AttachmentChip({ attachment }: { attachment: Attachment }) {
  return (
    <span
      className="border-border-subtle bg-surface-sunken text-ink-faint rounded border px-1.5 py-0.5 font-mono text-[10px]"
      title={attachment.media_type}
    >
      {attachment.kind === 'image' ? '▣' : '◻'} {attachment.label}
      {attachment.width ? ` · ${attachment.size}` : ''}
    </span>
  );
}

/** One-line hint of what a call was asked to do. */
function summarize(args: unknown): string {
  if (typeof args !== 'object' || args === null) return String(args);
  const entries = Object.entries(args as Record<string, unknown>);
  const first = entries.find(([, value]) => typeof value === 'string' && value.length > 0);
  if (first === undefined) return '';
  const text = String(first[1]).replace(/\s+/g, ' ');
  return text.length > 90 ? `${text.slice(0, 90)}…` : text;
}
