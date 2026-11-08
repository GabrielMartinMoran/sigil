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
import type { LoginState, RepositoryGroup, SessionSummary, State, TranscriptItem } from './types';

export type Phase = 'loading' | 'login' | 'ready' | 'error';

type Stream = {
  text: string;
  thinking: string;
  tools: ToolView[];
  batch: number;
  afterTools: boolean;
};

const emptyStream = (): Stream => ({ text: '', thinking: '', tools: [], batch: 0, afterTools: false });

type Store = {
  phase: Phase;
  status: LoginState | null;
  error: string;
  notice: string;

  state: State | null;
  items: TranscriptItem[];
  stream: Stream;
  busy: boolean;

  sessions: SessionSummary[];
  repositories: RepositoryGroup[];

  boot: () => Promise<void>;
  login: (password: string) => Promise<void>;
  logout: () => Promise<void>;

  refresh: () => Promise<void>;
  refetchTranscript: () => Promise<void>;
  loadSessions: () => Promise<void>;
  loadWorkspaces: () => Promise<void>;

  send: (text: string) => Promise<void>;
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

export const useStore = create<Store>((set, get) => ({
  phase: 'loading',
  status: null,
  error: '',
  notice: '',
  state: null,
  items: [],
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
    set({ phase: 'login', state: null, items: [], stream: emptyStream(), error: '', notice: '' });
  },

  async refresh() {
    const state = await api.state();
    set({ state, busy: state.busy });
  },

  async refetchTranscript() {
    const body = await api.transcript();
    set({ items: body.items, stream: emptyStream() });
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
    if (!trimmed) return;
    set({ error: '', notice: '' });
    try {
      const result = await api.send(trimmed);
      if (result.accepted === 'steer') {
        set({ notice: result.delivered ? 'Queued into the running turn.' : 'Could not queue it.' });
        return;
      }
      // The turn's user message travels as an event, but painting it now is what
      // makes the composer feel immediate. `refetchTranscript` replaces it.
      set({
        busy: true,
        items: [...get().items, { kind: 'user', index: -1, text: trimmed }],
        stream: emptyStream(),
      });
    } catch (error) {
      set({ error: report(error) });
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
      set({ items: [], stream: emptyStream() });
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

  async switchWorkspace(root) {
    try {
      await api.switchWorkspace(root);
      set({ items: [], stream: emptyStream() });
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
        return;
      case 'notice':
        set({ notice: text(data.text) });
        return;
      case 'question':
      case 'question_closed':
      case 'state_changed':
        void get()
          .refresh()
          .catch(() => undefined);
        return;
      case 'lagged':
        // The client fell behind: the transcript is the only safe thing to trust.
        void get()
          .refetchTranscript()
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
