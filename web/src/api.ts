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

export const api = {
  status: () => request<LoginState>('/api/status'),
  state: () => request<State>('/api/state'),

  login: (password: string) => post<{ ok: true }>('/api/login', { password }),
  logout: () => post<{ ok: true }>('/api/logout'),

  transcript: (limit?: number, before?: number) => {
    const query = new URLSearchParams();
    if (limit !== undefined) query.set('limit', String(limit));
    if (before !== undefined) query.set('before', String(before));
    const search = query.toString();
    return request<TranscriptPage>(`/api/session/transcript${search ? `?${search}` : ''}`);
  },
  sessions: () => request<{ current: string; sessions: SessionSummary[] }>('/api/sessions'),
  workspaces: () => request<{ repositories: RepositoryGroup[] }>('/api/workspaces'),

  send: (text: string, requestId: string) =>
    post<{
      accepted: 'turn' | 'steer';
      delivered?: boolean;
      request_id: string;
      duplicate?: boolean;
      state?: string;
    }>('/api/turn', {
      text,
      request_id: requestId,
    }),
  durable: () => request<DurableSession>('/api/session/durable'),
  abort: () => post<{ stopped: boolean }>('/api/abort'),
  answer: (id: string, answers: { question: string; answer: string; custom?: boolean }[] | null) =>
    post<{ resolved: boolean }>('/api/answer', { id, answers }),

  newSession: () => post<{ session: SessionView }>('/api/session/new'),
  openSession: (id: string) => post<{ session: SessionView }>('/api/session/open', { id }),
  switchWorkspace: (root: string) =>
    post<{ root: string; label: string; changed: boolean }>('/api/workspace', { root }),
};
