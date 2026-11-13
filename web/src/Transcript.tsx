import { useEffect, useRef } from 'react';

import { MarkdownText } from './Markdown';
import { Tools } from './Tools';
import { transcriptBlocks } from './toolPresentation';
import { durableSections, useStore } from './store';
import { BOTTOM_REACH, wantsNewestWindow, wantsOlderPage } from './transcriptWindow';
import type { ReadingFraction } from './transcriptWindow';
import type { Attachment, DurableEntry, DurableSession, TranscriptItem } from './types';

/**
 * The conversation, as a list of items.
 *
 * Three decisions:
 *
 * - **Items, not messages.** A tool call and its result travel as two of them with
 *   the same `call_id`; the presentation pairs them by ID and keeps missing results visible
 *   as interrupted calls rather than dropping them.
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
  /**
   * Where the reader was when an older page was asked for.
   *
   * The insertion lands above the viewport, so the scroll has to be moved by exactly
   * the height it added: `scrollHeight - (height - top)` puts the same row back on the
   * same screen line. `firstIndex` is part of the record so a request that never
   * arrived — a failure, or a window that was refetched instead — cannot apply a
   * stale adjustment later.
   */
  const pending = useRef<{ height: number; top: number; firstIndex: number } | null>(null);
  const reloading = useRef(false);
  const durable = useStore((store) => store.durable);
  const busy = useStore((store) => store.busy);
  const openRecovery = useStore((store) => store.openRecovery);
  const firstIndex = useStore((store) => store.firstIndex);

  useEffect(() => {
    const node = container.current;
    if (node === null) return;
    const onScroll = () => {
      const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
      stick.current = distance < BOTTOM_REACH;
      const state = useStore.getState();
      // Report first: the eviction the read below triggers is measured from here, and
      // a position that is only known on arrival would be the reader's old one.
      state.setReading(readingFraction(node));
      if (wantsOlderPage(node.scrollTop, state.hasMore, state.loadingOlder)) {
        pending.current = { height: node.scrollHeight, top: node.scrollTop, firstIndex: state.firstIndex };
        void state.loadOlder();
      } else if (wantsNewestWindow(distance, state) && !reloading.current) {
        // A page was let go below this reader and the route pages backwards only, so
        // the newest window is asked for again. Not a new cursor: the same read the
        // screen opened with, and the turn that may be streaming into the view is
        // kept — this reload is not the end of a turn.
        reloading.current = true;
        void state
          .refetchTranscript({ keepStream: true })
          .catch(() => undefined)
          .finally(() => {
            reloading.current = false;
          });
      }
    };
    node.addEventListener('scroll', onScroll, { passive: true });
    return () => node.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    const node = container.current;
    if (node === null) return;
    const held = pending.current;
    if (held !== null && held.firstIndex !== firstIndex) {
      // A page arrived above: keep the line the reader was on where it was.
      pending.current = null;
      node.scrollTop = node.scrollHeight - (held.height - held.top);
      // The insertion moved the window under the reader, not the reader: the fraction
      // that decides the next eviction is the one they are on now.
      useStore.getState().setReading(readingFraction(node));
      return;
    }
    if (stick.current) node.scrollTop = node.scrollHeight;
    // Report where the reader is, so an eviction lets go of the page furthest away.
    useStore.getState().setReading(readingFraction(node));
  }, [items, firstIndex]);

  return (
    <div ref={container} className="scroll-thin flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-4 px-3 py-5 sm:px-4">
        {items.length === 0 ? (
          <div className="flex flex-col items-center gap-5 py-16 text-center">
            {/* The same mark the terminal shows on an empty session, and the same
                silence about it: it is decoration, so it carries no alternative text. */}
            <img src="./sigil-logo.svg" alt="" aria-hidden="true" className="h-24 w-auto" />
            <p className="text-ink-faint text-sm">Nothing here yet. Ask something and it starts.</p>
          </div>
        ) : null}
        <DurableSections durable={durable} busy={busy} onOpenRecovery={openRecovery} />
        {transcriptBlocks(items).map((block, position) =>
          block.kind === 'tools' ? (
            <Tools key={block.id} tools={block.tools} />
          ) : (
            <Bubble key={`${block.item.index}-${position}`} item={block.item} />
          ),
        )}
      </div>
    </div>
  );
}

function entryLine(entry: DurableEntry) {
  return `#${entry.sequence} · ${entry.kind} · ${entry.state} · ${entry.text}`;
}

/**
 * Where the reader is, as the fraction of the scrollable content they have passed.
 *
 * Read from the scroll geometry, because the store cannot see the screen. The unit is
 * a fraction — 0 at the top, 1 at the bottom — and never an item: an item is what the
 * eviction is counted in, and `readingIndex` in `transcriptWindow.ts` is the only
 * place the two meet.
 */
function readingFraction(node: HTMLDivElement): ReadingFraction {
  const room = node.scrollHeight - node.clientHeight;
  if (room <= 0) return 0;
  return Math.min(Math.max(node.scrollTop / room, 0), 1);
}

function DurableSections({
  durable,
  busy,
  onOpenRecovery,
}: {
  durable: DurableSession | null;
  busy: boolean;
  onOpenRecovery: (id: string) => Promise<void>;
}) {
  if (durable === null) return null;
  const sections = durableSections(durable, busy);
  const terminal = sections.undelivered;
  return (
    <>
      {sections.pending.length > 0 ? (
        <section className="border-border-subtle bg-surface-sunken rounded-lg border p-3 text-sm">
          <h2 className="text-ink-muted font-mono text-[11px] tracking-wide uppercase">
            Pending · not in the conversation yet
          </h2>
          <ul className="text-ink-muted mt-2 space-y-1">
            {sections.pending.map((entry) => (
              <li key={entry.request_id}>{entryLine(entry)}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {terminal.length > 0 ? (
        <section className="border-border-subtle bg-surface-sunken rounded-lg border p-3 text-sm">
          <h2 className="text-ink-muted font-mono text-[11px] tracking-wide uppercase">Not delivered</h2>
          <ul className="text-ink-muted mt-2 space-y-1">
            {terminal.map((entry) => (
              <li key={entry.request_id}>{entryLine(entry)}</li>
            ))}
          </ul>
          <p className="text-ink-faint mt-2 text-xs">The server did not confirm these messages in the transcript.</p>
        </section>
      ) : null}
      {sections.conflicted.length > 0 ? (
        <section className="border-border-subtle bg-surface-sunken rounded-lg border p-3 text-sm">
          <h2 className="text-ink-muted font-mono text-[11px] tracking-wide uppercase">Delivered in a recovery copy</h2>
          <ul className="text-ink-muted mt-2 space-y-1">
            {sections.conflicted.map((entry) => (
              <li key={entry.request_id}>{entryLine(entry)}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {sections.recoveries.length > 0 ? (
        <section className="border-border-subtle bg-surface-sunken rounded-lg border p-3 text-sm">
          <h2 className="text-ink-muted font-mono text-[11px] tracking-wide uppercase">Recovery</h2>
          {sections.recoveries.map((recovery) => (
            <p key={recovery.recovery_session_id} className="text-ink-muted mt-2">
              {recovery.available ? (
                <>
                  A recovery copy is available.{' '}
                  <button
                    type="button"
                    disabled={!recovery.canOpen}
                    onClick={() => void onOpenRecovery(recovery.recovery_session_id)}
                    className="border-accent text-accent rounded border px-2 py-0.5 text-xs disabled:opacity-40"
                  >
                    Abrir recuperación
                  </button>
                </>
              ) : (
                <>A recovery copy existed but is no longer available.</>
              )}
            </p>
          ))}
        </section>
      ) : null}
    </>
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

  if (item.kind === 'subagent_report') {
    // A subagent's answer travels as a durable user input, so the service recognizes
    // the envelope and sends it as its own kind: painted as a bubble it would read as
    // something the reader typed.
    return (
      <details className="group text-ink-faint text-xs">
        <summary className="hover:text-ink-muted cursor-pointer font-mono select-none">
          ↩ {item.label ?? 'Subagent'} #{item.short_id ?? '?'} sent a report.
        </summary>
        <pre className="scroll-thin border-border-subtle mt-2 max-h-60 overflow-auto border-l pl-3 font-mono whitespace-pre-wrap">
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
