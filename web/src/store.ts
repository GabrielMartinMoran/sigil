/**
 * The client's state, in one store.
 *
 * The rule that keeps it small: **the server owns the facts, the client owns what is
 * in flight.** A turn's deltas accumulate in `stream` while it runs and are dropped
 * the moment the transcript is refetched, so there is never a second copy of the
 * conversation to keep in sync.
 */

import { create } from 'zustand';

import { ApiError, api, Unauthorized } from './api';
import { finishTool } from './toolPresentation';
import type { ToolView } from './toolPresentation';
import { TRANSCRIPT_WINDOW, clampReadingFraction, emptyWindow, newestWindow, withOlderPage } from './transcriptWindow';
import type { ReadingFraction } from './transcriptWindow';
import type {
  DurableEntry,
  DurableSession,
  LoginState,
  RepositoryGroup,
  SessionSummary,
  State,
  TranscriptItem,
} from './types';

export type Phase = 'loading' | 'login' | 'ready' | 'error';

type Stream = {
  text: string;
  thinking: string;
  tools: ToolView[];
  batch: number;
  afterTools: boolean;
};

const emptyStream = (): Stream => ({ text: '', thinking: '', tools: [], batch: 0, afterTools: false });

/** How finely a reading fraction is kept: a thousandth of the scroll, for stability. */
const READING_STEPS = 1000;

/** What a transcript reload is allowed to reset. */
type RefetchOptions = {
  /**
   * Keep the deltas of a running turn.
   *
   * A scroll can ask for the newest window in the middle of a turn; the text already
   * received is still the answer being written, so only the read that follows the end
   * of a turn may drop it.
   */
  keepStream?: boolean;
};

type Store = {
  phase: Phase;
  status: LoginState | null;
  error: string;
  notice: string;

  state: State | null;
  /** The mounted window of the conversation, newest page first mounted. */
  items: TranscriptItem[];
  /** Absolute position of `items[0]`: the `before` cursor for the page above it. */
  firstIndex: number;
  /** Whether the service says anything older exists. */
  hasMore: boolean;
  /** Item count of each mounted page, in screen order. */
  pages: number[];
  /** Items in the whole transcript: whether the window still ends at the end. */
  total: number;
  /**
   * Where the reader is in the mounted window, as the fraction of the scroll they
   * have passed: 0 at the top, 1 at the bottom.
   *
   * A fraction, not an index, because only the DOM knows where the scroll is and what
   * it measures is pixels. It decides which page an eviction lets go — the one
   * furthest from here — and `withOlderPage` is what turns it into an item.
   */
  readingFraction: ReadingFraction;
  /** One older page at a time, so a scroll that fires twice is still one read. */
  loadingOlder: boolean;
  /** Reports where the reader is, so an eviction can drop the page furthest away. */
  setReading: (readingFraction: ReadingFraction) => void;
  durable: DurableSession | null;
  pendingSend: { requestId: string; text: string } | null;
  stream: Stream;
  busy: boolean;

  sessions: SessionSummary[];
  repositories: RepositoryGroup[];

  boot: () => Promise<void>;
  login: (password: string) => Promise<void>;
  logout: () => Promise<void>;

  refresh: () => Promise<void>;
  refetchTranscript: (options?: RefetchOptions) => Promise<void>;
  loadOlder: () => Promise<void>;
  refreshDurable: () => Promise<void>;
  openRecovery: (id: string) => Promise<void>;
  loadSessions: () => Promise<void>;
  loadWorkspaces: () => Promise<void>;

  send: (text: string) => Promise<boolean>;
  abort: () => Promise<void>;
  answer: (id: string, answers: { question: string; answer: string; custom?: boolean }[] | null) => Promise<void>;
  newSession: () => Promise<void>;
  openSession: (id: string) => Promise<void>;
  switchWorkspace: (root: string) => Promise<void>;

  applyEvent: (type: string, data: Record<string, unknown>) => void;
  setUnauthenticated: () => void;
};

/**
 * A string from an event payload, without `[object Object]`.
 *
 * The payload arrives as `unknown` on purpose: the server is the one that decides
 * what it sends, and this client should not crash on a field that changed shape.
 * A non-primitive degrades to the empty string instead of being stringified into
 * something nobody can read.
 */
function text(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

/** Errors are shown, never thrown at the renderer. */
function report(error: unknown): string {
  if (error instanceof Unauthorized) return '';
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : String(error);
}

export type DurableSections = {
  pending: DurableEntry[];
  undelivered: DurableEntry[];
  conflicted: DurableEntry[];
  recoveries: { recovery_session_id: string; available: boolean; canOpen: boolean }[];
};

export function durableSections(durable: DurableSession | null, busy: boolean): DurableSections {
  if (durable === null) return { pending: [], undelivered: [], conflicted: [], recoveries: [] };
  return {
    pending: durable.pending,
    undelivered: durable.queue.filter(
      (entry) => (entry.state === 'stopped' || entry.state === 'interrupted') && !entry.delivered,
    ),
    conflicted: durable.conflicted_entries,
    recoveries: durable.recoveries.map((recovery) => ({
      ...recovery,
      canOpen: recovery.available && !busy,
    })),
  };
}

export const useStore = create<Store>((set, get) => ({
  phase: 'loading',
  status: null,
  error: '',
  notice: '',
  state: null,
  ...emptyWindow(),
  readingFraction: 0,
  loadingOlder: false,
  durable: null,
  pendingSend: null,
  stream: emptyStream(),
  busy: false,
  sessions: [],
  repositories: [],

  async boot() {
    try {
      const status = await api.status();
      set({ status });
      // The status route is public, so a 200 does not mean the cookie is valid. The
      // first authenticated call is what finds out, and its 401 lands in `boot`'s catch.
      await get().refresh();
      // The state is the shell; the conversation is its own route because it is the
      // one that grows. Loading both is what makes a reload show the session instead
      // of an empty screen with a title over it.
      await get().refetchTranscript();
      set({ phase: 'ready' });
      await Promise.all([get().loadSessions(), get().loadWorkspaces()]);
    } catch (error) {
      if (error instanceof Unauthorized) {
        set({ phase: 'login', error: '' });
        return;
      }
      set({ phase: 'error', error: report(error) });
    }
  },

  async login(password) {
    set({ error: '' });
    try {
      await api.login(password);
      await get().boot();
    } catch (error) {
      set({ error: report(error) || 'Wrong password.' });
    }
  },

  async logout() {
    await api.logout().catch(() => undefined);
    set({
      phase: 'login',
      state: null,
      ...emptyWindow(),
      loadingOlder: false,
      stream: emptyStream(),
      error: '',
      notice: '',
    });
  },

  async refresh() {
    const state = await api.state();
    set({ state, busy: state.busy });
  },

  async refetchTranscript({ keepStream = false }: RefetchOptions = {}) {
    // One window, not the whole conversation: a session of thousands of messages is
    // the reason this route pages at all.
    const page = await api.transcript(TRANSCRIPT_WINDOW);
    set({
      ...newestWindow(page),
      loadingOlder: false,
      // The deltas are the turn that is still typing, and this reload can be asked
      // for in the middle of one: a scroll that reaches the bottom asks for the
      // newest window again, and erasing the text already received would make the
      // answer vanish until the next delta. Only a reload that runs where the turn
      // has ended is allowed to drop them.
      stream: keepStream ? get().stream : emptyStream(),
    });
    void get().refreshDurable();
  },

  async loadOlder() {
    const { hasMore, loadingOlder, firstIndex, items, total, pages, readingFraction } = get();
    // `hasMore` is the service's answer, not a guess: at the start of a conversation
    // it stays false and nothing here asks again.
    if (!hasMore || loadingOlder || items.length === 0) return;
    set({ loadingOlder: true });
    try {
      const page = await api.transcript(TRANSCRIPT_WINDOW, firstIndex);
      // `readingFraction` decides which page leaves when the window is over its
      // bound: the one furthest from where the reader actually is, not always the
      // newest one. `withOlderPage` is where the fraction becomes an item.
      set({
        ...withOlderPage({ items, firstIndex, total, hasMore, pages }, page, readingFraction),
        loadingOlder: false,
      });
    } catch (error) {
      set({ loadingOlder: false, notice: report(error) });
    }
  },

  setReading(readingFraction) {
    // Quantized: a scroll event fires continuously and an eviction is decided in
    // whole items, so storing a new fraction per pixel would only repaint for
    // nothing. Clamped so the store never holds a position the window cannot have.
    const stable = Math.round(clampReadingFraction(readingFraction) * READING_STEPS) / READING_STEPS;
    if (get().readingFraction === stable) return;
    set({ readingFraction: stable });
  },

  async refreshDurable() {
    try {
      set({ durable: await api.durable() });
    } catch (error) {
      // A draft session has no row to project, and the service says so with a typed
      // 404. That is "nothing yet", a normal state — not a failure to report. What
      // must not survive it is the overlay: leaving the previous conversation's queue
      // and recoveries painted over an empty one is a lie about this session.
      if (error instanceof ApiError && error.status === 404) {
        set({ durable: null });
        return;
      }
      set({ notice: report(error) });
    }
  },

  async loadSessions() {
    try {
      const body = await api.sessions();
      set({ sessions: body.sessions });
    } catch (error) {
      set({ notice: report(error) });
    }
  },

  async loadWorkspaces() {
    try {
      const body = await api.workspaces();
      set({ repositories: body.repositories });
    } catch (error) {
      set({ notice: report(error) });
    }
  },

  async send(text) {
    const trimmed = text.trim();
    if (!trimmed) return false;
    const previous = get().pendingSend;
    const requestId = previous !== null && previous.text === trimmed ? previous.requestId : crypto.randomUUID();
    set({ error: '', notice: '', pendingSend: { requestId, text: trimmed } });
    try {
      const result = await api.send(trimmed, requestId);
      set({ pendingSend: null });
      if (result.duplicate === true) {
        set({ notice: 'This message was already accepted.' });
        void get().refetchTranscript();
        void get().refreshDurable();
        return true;
      }
      if (result.accepted === 'steer') {
        set({ notice: result.delivered ? 'Queued into the running turn.' : 'Could not queue it.' });
      } else {
        set({ busy: true, stream: emptyStream() });
      }
      void get().refreshDurable();
      return true;
    } catch (error) {
      set({ error: report(error) });
      return false;
    }
  },

  async abort() {
    await api.abort().catch((error: unknown) => set({ error: report(error) }));
  },

  async answer(id, answers) {
    await api.answer(id, answers).catch((error: unknown) => set({ error: report(error) }));
    await get().refresh();
  },

  async newSession() {
    try {
      await api.newSession();
      set({ ...emptyWindow(), loadingOlder: false, stream: emptyStream() });
      await get().refresh();
      await get().loadSessions();
    } catch (error) {
      set({ error: report(error) });
    }
  },

  async openSession(id) {
    try {
      await api.openSession(id);
      await get().refresh();
      await get().refetchTranscript();
      await get().loadSessions();
    } catch (error) {
      set({ error: report(error) });
    }
  },

  async openRecovery(id) {
    if (get().busy) {
      set({ notice: 'The server is busy; open the recovery when the turn ends.' });
      return;
    }
    await get().openSession(id);
  },

  async switchWorkspace(root) {
    try {
      await api.switchWorkspace(root);
      set({ ...emptyWindow(), loadingOlder: false, stream: emptyStream() });
      await get().refresh();
      await get().refetchTranscript();
      await Promise.all([get().loadSessions(), get().loadWorkspaces()]);
    } catch (error) {
      set({ error: report(error) });
    }
  },

  applyEvent(type, data) {
    switch (type) {
      case 'turn_started':
        set({ busy: true, stream: emptyStream() });
        return;
      case 'text_delta':
        set({
          stream: {
            ...get().stream,
            text: get().stream.text + text(data.text),
            afterTools: get().stream.tools.length > 0,
          },
        });
        return;
      case 'thinking_delta':
        set({
          stream: {
            ...get().stream,
            thinking: get().stream.thinking + text(data.text),
            afterTools: get().stream.tools.length > 0,
          },
        });
        return;
      case 'tool_call': {
        const stream = get().stream;
        const batch = stream.batch + (stream.afterTools ? 1 : 0);
        const tools: ToolView[] = [
          ...stream.tools,
          {
            id: text(data.invocation_id) || `live-${text(data.seq) || String(stream.tools.length)}`,
            name: text(data.tool_name),
            phase: 'running',
            args: data.args,
            output: '',
            started: Date.now(),
            batch,
          },
        ];
        set({ stream: { ...stream, tools, batch, afterTools: false } });
        return;
      }
      case 'tool_finished': {
        set({ stream: { ...get().stream, tools: finishTool(get().stream.tools, data) } });
        return;
      }
      case 'turn_finished':
        set({ busy: false });
        // The deltas were the typing; the transcript is the record.
        void get()
          .refetchTranscript()
          .then(() => get().refresh())
          .then(() => get().loadSessions())
          .catch(() => undefined);
        void get().refreshDurable();
        return;
      case 'steer_delivered':
        void get().refreshDurable();
        return;
      case 'notice':
        set({ notice: text(data.text) });
        return;
      case 'question':
      case 'question_closed':
      case 'state_changed':
        void get()
          .refresh()
          .then(() => get().refreshDurable())
          .catch(() => undefined);
        return;
      case 'lagged':
        // The client fell behind: the transcript and overlay are the safe records.
        void get()
          .refetchTranscript()
          .then(() => get().refreshDurable())
          .then(() => get().refresh())
          .catch(() => undefined);
        return;
      default:
        return;
    }
  },

  setUnauthenticated() {
    if (get().phase === 'login') return;
    set({ phase: 'login', error: 'Your session expired. Sign in again.' });
  },
}));
