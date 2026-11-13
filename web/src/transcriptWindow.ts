/**
 * The window of the conversation the client paints.
 *
 * A session of thousands of messages cannot be held by a browser tab, and it does not
 * have to be: the service answers one page at a time (`GET
 * /api/session/transcript?limit=&before=`), and this module is the client's half of
 * that contract — where the window starts, which page leaves when the reader has
 * scrolled past the bound, and whether the bottom of the window is still the end of
 * the conversation. It is pure data in and pure data out, so every rule here can be
 * tested without a store, a DOM or a request.
 *
 * Four decisions:
 *
 * - **The newest page is the window.** A screen opens where the conversation is, not
 *   where it started, and everything older arrives only when the reader goes up.
 * - **An item keeps its absolute `index`.** A click maps that index to a rollback
 *   point; renumbering a page would send the reader to the wrong turn. The window
 *   therefore never rewrites an item, it only decides which ones it holds.
 * - **The page that leaves is the one furthest from the reading position.** Not
 *   always the newest one: a reader who went back down and kept reading lower is
 *   furthest from the top. Distance, not age, is what makes an eviction invisible —
 *   and a page the reader is inside is never the one dropped while another is
 *   further away.
 * - **What leaves the bottom is asked for again.** The service pages backwards only,
 *   so there is no `after` cursor to walk down with: when the reader reaches the
 *   bottom of a window that does not end at the end of the conversation,
 *   `reachesEnd` is false and the client asks for the newest window again instead of
 *   inventing a cursor. The streaming block and the queue's waiting rows belong to
 *   no page here — the stream lives in its own store field and the waiting rows come
 *   from the durable read — so a window that drops a page cannot lose either.
 */

import type { TranscriptItem } from './types';

/**
 * Items one page of the conversation holds.
 *
 * Mirrors `DEFAULT_TRANSCRIPT_WINDOW` in `src/engine/preferences.py`, which is what
 * the terminal reads from `ui.json`. The web client has no settings surface of its
 * own, so this is the one place its window size is written down: enough that a
 * screenful of a long conversation arrives in one request, small enough that a
 * session of thousands of messages is never held at once.
 */
export const TRANSCRIPT_WINDOW = 100;

/** How many pages the client keeps mounted, as the terminal's `ChatLog` does. */
export const MAX_MOUNTED_PAGES = 3;

/** How close to the bottom, in CSS pixels, counts as reaching it. */
export const BOTTOM_REACH = 80;

/** The fields of a page the window is built from. */
export type PageSlice = {
  items: TranscriptItem[];
  first_index: number;
  has_more: boolean;
  total: number;
};

/** The mounted window: the items, where it starts, and the pages it is made of. */
export type TranscriptWindow = {
  items: TranscriptItem[];
  /** Absolute position of `items[0]` in the whole transcript; the `before` cursor. */
  firstIndex: number;
  /** Items in the whole transcript, so the window can tell whether it ends at the end. */
  total: number;
  /** Whether the service says anything older exists. */
  hasMore: boolean;
  /** Item count of each mounted page, in the order the pages appear on screen. */
  pages: number[];
};

/** A window with nothing in it: a new session, or a transcript not read yet. */
export function emptyWindow(): TranscriptWindow {
  return { items: [], firstIndex: 0, total: 0, hasMore: false, pages: [] };
}

/** The window a fresh read of the newest page leaves behind. */
export function newestWindow(page: PageSlice): TranscriptWindow {
  return {
    items: page.items,
    firstIndex: page.first_index,
    total: page.total,
    hasMore: page.has_more,
    pages: page.items.length === 0 ? [] : [page.items.length],
  };
}

/**
 * Whether the bottom of the window is the end of the conversation.
 *
 * False means a page was let go below the reader, and since the route pages
 * backwards there is no cursor that would bring it back: the honest repair is the
 * newest window again, which is what the transcript component asks for when the
 * reader reaches the bottom.
 */
export function reachesEnd(window: Pick<TranscriptWindow, 'items' | 'firstIndex' | 'total'>): boolean {
  return window.firstIndex + window.items.length >= window.total;
}

/**
 * Whether the window should ask for the page above it.
 *
 * The reader is at the top, nothing is in flight and the service says there is more:
 * all three, because a scroll event fires many times and only one of those reads may
 * become a request.
 */
export function wantsOlderPage(scrollTop: number, hasMore: boolean, loadingOlder: boolean): boolean {
  return scrollTop <= 0 && hasMore && !loadingOlder;
}

/**
 * Whether the bottom of the window needs the newest page asked for again.
 *
 * True only for a reader who has reached the bottom of a window that no longer ends
 * at the end of the conversation — the case an eviction creates, and the one the
 * route cannot answer with a cursor because it pages backwards only.
 */
export function wantsNewestWindow(
  distanceFromBottom: number,
  window: Pick<TranscriptWindow, 'items' | 'firstIndex' | 'total'>,
): boolean {
  return distanceFromBottom <= BOTTOM_REACH && window.items.length > 0 && !reachesEnd(window);
}

/**
 * The window after a page arrives above it, keeping at most `MAX_MOUNTED_PAGES`.
 *
 * `reading` is the position in the resulting items the reader is looking at, and it
 * decides which page leaves: the one furthest from it. A reader at the top (the
 * usual case — that is what asks for an older page) drops the newest page; a reader
 * who has come back down drops the page at the top, which `before` can fetch again.
 * Ties go to the front for the same reason: the top of the window is the side with a
 * cursor.
 *
 * An empty page carries no items to show but still carries the answer, so it closes
 * the walk without growing the window.
 */
export function withOlderPage(window: TranscriptWindow, page: PageSlice, reading = 0): TranscriptWindow {
  if (page.items.length === 0) return { ...window, hasMore: page.has_more };
  let items = [...page.items, ...window.items];
  let pages = [page.items.length, ...window.pages];
  let firstIndex = page.first_index;
  let droppedFromTop = false;
  const position = Math.min(Math.max(reading, 0), Math.max(items.length - 1, 0));
  while (pages.length > MAX_MOUNTED_PAGES) {
    const drop = farthestPage(pages, position);
    const count = pages[drop] ?? 0;
    const start = pages.slice(0, drop).reduce((total, size) => total + size, 0);
    items = [...items.slice(0, start), ...items.slice(start + count)];
    pages = [...pages.slice(0, drop), ...pages.slice(drop + 1)];
    if (drop === 0) {
      // The window now starts where the page after it starts, and the dropped page is
      // itself the proof that something older exists.
      firstIndex += count;
      droppedFromTop = true;
    }
  }
  return {
    items,
    firstIndex,
    total: page.total,
    hasMore: droppedFromTop ? true : page.has_more,
    pages,
  };
}

/**
 * Index of the page furthest from `position`, measured to its nearest edge.
 *
 * A page containing the position is at distance 0 and cannot win unless every page
 * does, which cannot happen with more than one page mounted. Ties go to the lowest
 * index: the top of the window is the side a cursor can walk back into.
 */
function farthestPage(pages: number[], position: number): number {
  let best = 0;
  let bestDistance = -1;
  let start = 0;
  for (const [index, count] of pages.entries()) {
    const distance =
      position < start ? start - position : position >= start + count ? position - (start + count - 1) : 0;
    if (distance > bestDistance) {
      best = index;
      bestDistance = distance;
    }
    start += count;
  }
  return best;
}
