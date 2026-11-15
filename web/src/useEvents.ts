/**
 * The event stream.
 *
 * `EventSource` is used instead of a hand-rolled reader for one reason: it
 * reconnects by itself and resends `Last-Event-ID`, which is exactly the `after`
 * the server replays from. What it does not do is survive a reload of the page or a
 * server restart with a new cookie, so the effect is keyed on authentication: a new
 * login opens a new stream, and the store refetches the state on `open` to cover
 * whatever happened while it was closed.
 */

import { useEffect } from 'react';

import { setUnauthorizedHandler } from './api';
import { closeAttention, createAttentionListener } from './notifications';
import { isCurrentSession } from './sessionSelection';
import { useStore } from './store';

export function useEvents(active: boolean): void {
  const sessionId = useStore((store) => store.selectedSessionId);
  const generation = useStore((store) => store.selectionGeneration);
  const applyEvent = useStore((store) => store.applyEvent);
  const setUnauthenticated = useStore((store) => store.setUnauthenticated);

  useEffect(() => {
    setUnauthorizedHandler(setUnauthenticated);
    return () => setUnauthorizedHandler(null);
  }, [setUnauthenticated]);

  useEffect(() => {
    if (!active || !sessionId) return;
    let disposed = false;
    const current = () => !disposed && isCurrentSession(useStore.getState(), sessionId, generation);

    const attention = createAttentionListener(useStore.getState().state?.seq ?? 0, () => {
      const state = useStore.getState().state;
      if (!state) return undefined;
      const workspace = state.workspace;
      const project = workspace.repository
        ? workspace.is_worktree
          ? `${workspace.repository}:${workspace.label}`
          : workspace.repository
        : workspace.label;
      return { project, conversation: state.session.title || `Session ${state.session.short_id}` };
    });
    const source = new EventSource(
      `/events?session_id=${encodeURIComponent(sessionId)}&after=${useStore.getState().state?.seq ?? 0}`,
      { withCredentials: true },
    );
    let opened = false;

    const listen = (type: string) => {
      source.addEventListener(type, (event) => {
        if (!current()) return;
        const payload = JSON.parse((event as MessageEvent<string>).data) as Record<string, unknown>;
        attention(type, payload);
        applyEvent(type, payload);
      });
    };

    // Every type the server publishes. A missing one is a fact that never arrives, so
    // the list is kept next to the store's switch on purpose: `applyEvent` ignores an
    // unknown type, which would hide a typo here.
    for (const type of [
      'turn_started',
      'text_delta',
      'thinking_delta',
      'tool_call',
      'tool_result',
      'tool_finished',
      'steer_delivered',
      'turn_finished',
      'notice',
      'state_changed',
      'tree_changed',
      'question',
      'question_closed',
      'lagged',
    ]) {
      listen(type);
    }

    source.addEventListener('open', () => {
      // The first open is the boot's own state fetch; a later one is a reconnection,
      // and what it missed is worth re-reading.
      if (opened && current())
        void useStore
          .getState()
          .refresh()
          .then(() => {
            if (current()) useStore.getState().applyEvent('state_changed', {});
          })
          .catch(() => undefined);
      opened = true;
    });

    let recovering = false;
    source.addEventListener('error', () => {
      // `EventSource` retries on its own; a 401 is the one case where it will not
      // succeed, and the store decides what that means.
      if (!current() || recovering) return;
      recovering = true;
      void useStore
        .getState()
        .refresh()
        .then(() => {
          if (!current()) return;
          useStore.getState().applyEvent('lagged', {});
          if (source.readyState === EventSource.CLOSED) {
            useStore.setState({ selectionGeneration: generation + 1 });
          }
        })
        .catch(() => undefined)
        .finally(() => {
          recovering = false;
        });
    });

    return () => {
      disposed = true;
      source.close();
      closeAttention();
    };
  }, [active, sessionId, generation, applyEvent, setUnauthenticated]);
}
