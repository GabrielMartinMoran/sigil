/**
 * The HTTP client. Thin on purpose: it knows the routes and the error envelope, and
 * it decides nothing.
 *
 * Every call goes through `request`, so there is exactly one place that handles the
 * two things that can happen to all of them: the session expired (401 → the app
 * shows the login form again) and the server answered with `{error}` instead of the
 * expected shape.
 */

import type {
  AgentView,
  DurableSession,
  LoginState,
  RepositoryGroup,
  SessionSummary,
  SessionView,
  State,
  TranscriptPage,
} from './types';

export class Unauthorized extends Error {
  constructor() {
    super('unauthenticated');
    this.name = 'Unauthorized';
  }
}

/** A call the server refused, with the message it gave. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

let onUnauthorized: (() => void) | null = null;

/** Registers what to do when the cookie is gone. The app re-renders the login. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: init?.body ? { 'content-type': 'application/json' } : undefined,
    ...init,
  });

  if (response.status === 401) {
    onUnauthorized?.();
    throw new Unauthorized();
  }

  const text = await response.text();
  const body: unknown = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const message =
      body !== null && typeof body === 'object' && 'error' in body ? String(body.error) : `HTTP ${response.status}`;
    throw new ApiError(message, response.status);
  }

  return body as T;
}

const post = <T>(path: string, payload?: unknown): Promise<T> =>
  request<T>(path, { method: 'POST', body: payload === undefined ? undefined : JSON.stringify(payload) });

function scoped(path: string, sessionId?: string | null): string {
  if (!sessionId) return path;
  return `${path}${path.includes('?') ? '&' : '?'}session_id=${encodeURIComponent(sessionId)}`;
}

export const api = {
  status: () => request<LoginState>('/api/status'),
  state: (sessionId?: string | null) => request<State>(scoped('/api/state', sessionId)),

  login: (password: string) => post<{ ok: true }>('/api/login', { password }),
  logout: () => post<{ ok: true }>('/api/logout'),

  transcript: (limit?: number, before?: number, sessionId?: string | null) => {
    const query = new URLSearchParams();
    if (limit !== undefined) query.set('limit', String(limit));
    if (before !== undefined) query.set('before', String(before));
    if (sessionId) query.set('session_id', sessionId);
    const search = query.toString();
    return request<TranscriptPage>(`/api/session/transcript${search ? `?${search}` : ''}`);
  },
  sessions: (sessionId?: string | null) =>
    request<{ current: string; sessions: SessionSummary[] }>(scoped('/api/sessions', sessionId)),
  workspaces: (sessionId?: string | null) =>
    request<{ repositories: RepositoryGroup[] }>(scoped('/api/workspaces', sessionId)),

  send: (text: string, requestId: string, sessionId?: string | null) =>
    post<{
      accepted: 'turn' | 'steer';
      delivered?: boolean;
      request_id: string;
      duplicate?: boolean;
      state?: string;
    }>(scoped('/api/turn', sessionId), {
      text,
      request_id: requestId,
    }),
  durable: (sessionId?: string | null) => request<DurableSession>(scoped('/api/session/durable', sessionId)),
  abort: (sessionId?: string | null) => post<{ stopped: boolean }>(scoped('/api/abort', sessionId)),
  answer: (
    id: string,
    answers: { question: string; answer: string; custom?: boolean }[] | null,
    sessionId?: string | null,
  ) => post<{ resolved: boolean }>(scoped('/api/answer', sessionId), { id, answers }),

  selectAgent: (name: string, sessionId?: string | null) =>
    post<{ agent: AgentView; agents: AgentView[] }>(scoped('/api/agent', sessionId), { name }),

  newSession: (sessionId?: string | null) => post<{ session: SessionView }>(scoped('/api/session/new', sessionId)),
  openSession: (id: string, sessionId?: string | null) =>
    post<{ session: SessionView }>(scoped('/api/session/open', sessionId), { id }),
  switchWorkspace: (root: string, sessionId?: string | null) =>
    post<{ root: string; label: string; changed: boolean; session: SessionView }>(scoped('/api/workspace', sessionId), {
      root,
    }),
};
