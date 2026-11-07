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

import { api, setUnauthorizedHandler } from './api';
import { useStore } from './store';

export function useEvents(active: boolean): void {
  const applyEvent = useStore((store) => store.applyEvent);
  const setUnauthenticated = useStore((store) => store.setUnauthenticated);

  useEffect(() => {
    setUnauthorizedHandler(setUnauthenticated);
    return () => setUnauthorizedHandler(null);
  }, [setUnauthenticated]);

  useEffect(() => {
    if (!active) return;

    const source = new EventSource('/events', { withCredentials: true });
    let opened = false;

    const listen = (type: string) => {
      source.addEventListener(type, (event) => {
        const payload = JSON.parse((event as MessageEvent<string>).data) as Record<string, unknown>;
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
      if (opened) void api.state().then(() => useStore.getState().applyEvent('state_changed', {}));
      opened = true;
    });

    source.addEventListener('error', () => {
      // `EventSource` retries on its own; a 401 is the one case where it will not
      // succeed, and the store decides what that means.
      if (source.readyState === EventSource.CLOSED) setUnauthenticated();
    });

    return () => source.close();
  }, [active, applyEvent, setUnauthenticated]);
}
