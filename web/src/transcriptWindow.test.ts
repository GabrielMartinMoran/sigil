import assert from 'node:assert/strict';
import { beforeEach, test } from 'node:test';

import { useStore } from './store';
import {
  BOTTOM_REACH,
  MAX_MOUNTED_PAGES,
  TRANSCRIPT_WINDOW,
  newestWindow,
  reachesEnd,
  readingIndex,
  wantsNewestWindow,
  wantsOlderPage,
  withOlderPage,
} from './transcriptWindow';
import type { PageSlice, TranscriptWindow } from './transcriptWindow';
import type { TranscriptItem } from './types';

function item(index: number): TranscriptItem {
  return { kind: 'assistant', index, text: `item ${index}` };
}

/** One page of `count` items that starts at the absolute position `firstIndex`. */
function page(firstIndex: number, count: number, hasMore: boolean, total = 1000): PageSlice {
  return {
    items: Array.from({ length: count }, (_, offset) => item(firstIndex + offset)),
    first_index: firstIndex,
    has_more: hasMore,
    total,
  };
}

function indexes(items: TranscriptItem[]): number[] {
  return items.map((entry) => entry.index);
}

// --- the window algebra, without a store or a DOM ---------------------------

void test('the newest page becomes the whole window and ends at the end of it', () => {
  const window = newestWindow(page(900, 3, true, 903));

  assert.deepEqual(indexes(window.items), [900, 901, 902]);
  assert.equal(window.firstIndex, 900);
  assert.equal(window.hasMore, true);
  assert.deepEqual(window.pages, [3]);
  assert.equal(reachesEnd(window), true);
});

void test('an older page is prepended and its cursor becomes the window start', () => {
  const window = withOlderPage(newestWindow(page(900, 3, true, 903)), page(897, 3, true, 903));

  assert.deepEqual(indexes(window.items), [897, 898, 899, 900, 901, 902]);
  assert.equal(window.firstIndex, 897);
  assert.equal(window.hasMore, true);
  assert.deepEqual(window.pages, [3, 3]);
});

void test('reading at the top drops the page furthest away, which is the newest one', () => {
  let window = newestWindow(page(10, 3, true, 13));
  for (const older of [page(7, 3, true, 13), page(4, 3, true, 13), page(1, 3, true, 13)]) {
    window = withOlderPage(window, older, 0);
  }

  assert.equal(window.pages.length, MAX_MOUNTED_PAGES);
  assert.deepEqual(indexes(window.items), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.equal(window.firstIndex, 1);
  // The newest page was let go below the reader: the window no longer ends there, and
  // that is what asks for the newest window again when they come back down.
  assert.equal(reachesEnd(window), false);
});

void test('reading near the bottom drops the page at the top, not the one being read', () => {
  let window = newestWindow(page(10, 3, true, 13));
  for (const older of [page(7, 3, true, 13), page(4, 3, true, 13)]) {
    window = withOlderPage(window, older, 0);
  }
  // Now 9 items: 1..9 with the reader on the last one. The page at the top is the
  // furthest away, and `before` can walk back into it.
  window = withOlderPage(window, page(1, 3, true, 13), 8);

  assert.deepEqual(indexes(window.items), [4, 5, 6, 7, 8, 9, 10, 11, 12]);
  assert.equal(window.firstIndex, 4);
  assert.equal(window.hasMore, true, 'the page just dropped is proof that older items exist');
  assert.equal(reachesEnd(window), true, 'the newest content stayed mounted');
});

void test('a page that says there is nothing older ends the walk', () => {
  const window = withOlderPage(newestWindow(page(2, 3, true, 5)), page(0, 2, false, 5), 0);

  assert.equal(window.hasMore, false);
  assert.deepEqual(indexes(window.items), [0, 1, 2, 3, 4]);
  assert.equal(reachesEnd(window), true);
});

void test('an empty older page changes no item and closes the walk', () => {
  const window = withOlderPage(
    newestWindow(page(2, 3, true, 5)),
    { items: [], first_index: 0, has_more: false, total: 5 },
    0,
  );

  assert.deepEqual(indexes(window.items), [2, 3, 4]);
  assert.deepEqual(window.pages, [3]);
  assert.equal(window.hasMore, false);
});

void test('a reading fraction becomes an item index in one place, and only there', () => {
  // A 300-item window: both ends are exact, the middle is the middle item, and a
  // position outside the window is impossible instead of becoming a cursor.
  assert.equal(readingIndex(0, 300), 0);
  assert.equal(readingIndex(1, 300), 299);
  assert.equal(readingIndex(0.5, 300), 150, 'round(0.5 * 299)');
  assert.equal(readingIndex(-0.4, 300), 0, 'a scroll cannot be above the top');
  assert.equal(readingIndex(9, 300), 299, 'nor below the bottom');
  assert.equal(readingIndex(Number.NaN, 300), 0, 'a geometry that could not be measured');
  assert.equal(readingIndex(0.5, 0), 0, 'an empty window has no item to point at');

  // The unit is a fraction, so the same number as an index would name a different
  // item: that mismatch is the defect this pins.
  assert.equal(readingIndex(0.99, 1000), 989);
  assert.notEqual(readingIndex(0.5, 300), 0.5, 'a fraction is never handed on as an index');
});

/** A window three pages deep: 300 items, 200..499 of a 500-item transcript. */
function threePages(): TranscriptWindow {
  let window = newestWindow(page(400, 100, true, 500));
  window = withOlderPage(window, page(300, 100, true, 500), 0);
  return withOlderPage(window, page(200, 100, true, 500), 0);
}

void test('the eviction is decided in items, from the fraction the component measures', () => {
  const window = threePages();
  const incoming = page(100, 100, true, 500);
  assert.equal(window.items.length, 300);

  // At the top the reader is furthest from the newest page, so that one leaves and
  // the window stops ending where the conversation ends.
  const top = withOlderPage(window, incoming, 0);
  assert.equal(indexes(top.items)[0], 100);
  assert.equal(indexes(top.items).slice(-1)[0], 399);
  assert.equal(reachesEnd(top), false);

  // At the bottom the newest page is the one the reader is inside, so the page at
  // the top leaves. This is the reader the old unit sent to the eviction as 1000.
  const bottom = withOlderPage(window, incoming, 1);
  assert.equal(indexes(bottom.items)[0], 200);
  assert.equal(indexes(bottom.items).slice(-1)[0], 499);
  assert.equal(reachesEnd(bottom), true, 'the newest page is never lost under a reader at the bottom');

  // Halfway down: round(0.5 * 399) = 200, which is inside the third page. The top
  // page is then the furthest away, and one item decides it — 199 would have made
  // the newest page the furthest one instead.
  const middle = withOlderPage(window, incoming, 0.5);
  assert.equal(readingIndex(0.5, 400), 200);
  assert.equal(indexes(middle.items)[0], 200);
  assert.equal(indexes(middle.items).slice(-1)[0], 499);
  assert.equal(reachesEnd(middle), true);

  // The regression, at the fraction a reader working through old content reports:
  // 0.2 is item 80, inside the page that just arrived. As an index it would have
  // been the second item, and as a value out of a thousand it saturated at the
  // bottom — which is how the page being read came to be the page discarded.
  const reading = withOlderPage(window, incoming, 0.2);
  assert.equal(readingIndex(0.2, 400), 80);
  assert.equal(indexes(reading.items)[0], 100, 'the page being read stays mounted');
  assert.equal(indexes(reading.items).slice(-1)[0], 399);
  assert.equal(reachesEnd(reading), false, 'the newest page is what that reader is furthest from');
});

// --- the store, with the routes it calls stubbed ----------------------------

let requests: string[] = [];

/** The page the stub answers with, keyed by the `before` it was asked for. */
function stubTranscript(before: number | null, count: number, hasMore: boolean, total = 1000): void {
  globalThis.fetch = (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.pathname : '';
    if (!url.startsWith('/api/session/transcript')) {
      return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }));
    }
    requests.push(url);
    const start = before ?? total - count;
    const items = Array.from({ length: count }, (_, offset) => item(start + offset));
    return Promise.resolve(
      new Response(
        JSON.stringify({
          session: {},
          items,
          first_index: start,
          has_more: hasMore,
          total,
          skills: [],
          pending: [],
        }),
        { status: 200 },
      ),
    );
  };
}

beforeEach(() => {
  requests = [];
  stubTranscript(null, 3, true);
  useStore.setState({
    ...newestWindow({ items: [], first_index: 0, has_more: false, total: 0 }),
    readingFraction: 0,
    loadingOlder: false,
    durable: null,
    busy: false,
    error: '',
    notice: '',
    stream: { text: '', thinking: '', tools: [], batch: 0, afterTools: false },
  });
});

void test('the initial load asks for one window and paints only that page', async () => {
  await useStore.getState().refetchTranscript();

  assert.deepEqual(requests, [`/api/session/transcript?limit=${TRANSCRIPT_WINDOW}`]);
  assert.deepEqual(indexes(useStore.getState().items), [997, 998, 999]);
  assert.equal(useStore.getState().hasMore, true);
});

void test('scrolling up asks for the page above the window and prepends it', async () => {
  await useStore.getState().refetchTranscript();
  stubTranscript(994, 3, true);

  await useStore.getState().loadOlder();

  assert.deepEqual(requests, [
    `/api/session/transcript?limit=${TRANSCRIPT_WINDOW}`,
    `/api/session/transcript?limit=${TRANSCRIPT_WINDOW}&before=997`,
  ]);
  // Absolute indexes survive the insertion: a click still names its rollback point.
  assert.deepEqual(indexes(useStore.getState().items), [994, 995, 996, 997, 998, 999]);
  assert.equal(useStore.getState().firstIndex, 994);
});

void test('only one older page is in flight at a time', async () => {
  await useStore.getState().refetchTranscript();
  stubTranscript(997, 3, true);

  await Promise.all([useStore.getState().loadOlder(), useStore.getState().loadOlder()]);

  assert.equal(requests.length, 2);
  assert.equal(useStore.getState().loadingOlder, false);
});

void test('a window that already holds the start of the conversation asks for nothing', async () => {
  stubTranscript(0, 3, false, 3);
  await useStore.getState().refetchTranscript();

  await useStore.getState().loadOlder();

  assert.equal(requests.length, 1);
  assert.deepEqual(indexes(useStore.getState().items), [0, 1, 2]);
});

void test('a refetched transcript replaces the window instead of stacking on it', async () => {
  await useStore.getState().refetchTranscript();
  stubTranscript(997, 3, true);
  await useStore.getState().loadOlder();

  stubTranscript(null, 3, true, 5000);
  await useStore.getState().refetchTranscript();

  assert.deepEqual(indexes(useStore.getState().items), [4997, 4998, 4999]);
  assert.equal(useStore.getState().firstIndex, 4997);
  assert.deepEqual(useStore.getState().pages, [3]);
});

void test('the page the reader is on decides which one an eviction lets go', async () => {
  // Reader at the top the whole way: the newest page leaves, and the window stops
  // reaching the end of the conversation.
  stubTranscript(null, 3, true, 13);
  await useStore.getState().refetchTranscript();
  for (const [start, readingFraction] of [
    [7, 0],
    [4, 0],
    [1, 0],
  ] as const) {
    stubTranscript(start, 3, true, 13);
    useStore.getState().setReading(readingFraction);
    await useStore.getState().loadOlder();
  }

  assert.deepEqual(indexes(useStore.getState().items), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.equal(reachesEnd(useStore.getState()), false);

  // The reader comes back down. The newest window is asked for again with no cursor:
  // the route pages backwards, so a fresh newest page is the only honest repair.
  requests = [];
  stubTranscript(null, 3, true, 13);
  await useStore.getState().refetchTranscript();

  assert.deepEqual(requests, [`/api/session/transcript?limit=${TRANSCRIPT_WINDOW}`]);
  assert.deepEqual(indexes(useStore.getState().items), [10, 11, 12]);
  assert.equal(reachesEnd(useStore.getState()), true);
});

void test('a reader who stayed at the bottom keeps the newest page mounted', async () => {
  stubTranscript(null, 3, true, 13);
  await useStore.getState().refetchTranscript();
  for (const [start, readingFraction] of [
    [7, 0],
    [4, 1],
    [1, 1],
  ] as const) {
    stubTranscript(start, 3, true, 13);
    useStore.getState().setReading(readingFraction);
    await useStore.getState().loadOlder();
  }

  assert.equal(reachesEnd(useStore.getState()), true);
  assert.deepEqual(indexes(useStore.getState().items).slice(-3), [10, 11, 12]);
});

// --- what a reload is allowed to reset -------------------------------------

void test('a reload asked for by a scroll keeps the turn that is still streaming', async () => {
  await useStore.getState().refetchTranscript();
  useStore.setState({
    stream: { text: 'half an answer', thinking: 'why', tools: [], batch: 0, afterTools: false },
  });
  stubTranscript(null, 3, true, 5000);

  await useStore.getState().refetchTranscript({ keepStream: true });

  assert.deepEqual(indexes(useStore.getState().items), [4997, 4998, 4999], 'the newest window is still painted');
  assert.equal(useStore.getState().stream.text, 'half an answer', 'the deltas belong to the running turn');
  assert.equal(useStore.getState().stream.thinking, 'why');
});

void test('the reload that ends a turn drops the deltas, because the transcript is the record', async () => {
  useStore.setState({
    stream: { text: 'half an answer', thinking: 'why', tools: [], batch: 0, afterTools: false },
  });

  await useStore.getState().refetchTranscript();

  assert.equal(useStore.getState().stream.text, '');
  assert.equal(useStore.getState().stream.thinking, '');
});

// --- the two scroll decisions, also without a DOM ---------------------------
void test('the top edge asks for an older page only when one can arrive', () => {
  assert.equal(wantsOlderPage(0, true, false), true);
  assert.equal(wantsOlderPage(0, false, false), false, 'the service says there is nothing older');
  assert.equal(wantsOlderPage(0, true, true), false, 'one page in flight is enough');
  assert.equal(wantsOlderPage(40, true, false), false, 'not at the top yet');
});

void test('the bottom asks for the newest window only when the end is missing', () => {
  const partial = { items: [item(1), item(2)], firstIndex: 0, total: 3 };
  assert.equal(reachesEnd(partial), false);
  assert.equal(wantsNewestWindow(0, partial), true);
  assert.equal(wantsNewestWindow(BOTTOM_REACH + 1, partial), false, 'not at the bottom yet');

  const whole = { items: [item(1), item(2)], firstIndex: 1, total: 3 };
  assert.equal(reachesEnd(whole), true);
  assert.equal(wantsNewestWindow(0, whole), false, 'the end of the conversation is mounted');

  assert.equal(wantsNewestWindow(0, { items: [], firstIndex: 0, total: 3 }), false, 'nothing painted yet');
});
