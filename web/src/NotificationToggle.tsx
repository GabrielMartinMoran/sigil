import { useId, useState, useSyncExternalStore } from 'react';

import { notificationState, setNotificationsEnabled, subscribeNotifications } from './notifications';

export function NotificationToggle() {
  const state = useSyncExternalStore(subscribeNotifications, notificationState);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const statusId = useId();
  const enabled = state === 'enabled';
  const hint =
    state === 'blocked'
      ? 'Notifications are blocked. Change this site’s notification permission in your browser settings.'
      : state === 'unsupported'
        ? 'Desktop notifications need a supported browser and HTTPS or localhost.'
        : '';

  function toggle(): void {
    setPending(true);
    setError('');
    void setNotificationsEnabled(!enabled).then((allowed) => {
      if (enabled ? notificationState() === 'enabled' : !allowed)
        setError('Alert settings could not be saved. Check notification permission and site storage settings.');
      setPending(false);
    });
  }

  return (
    <div className="relative shrink-0">
      <button
        type="button"
        aria-pressed={enabled}
        aria-busy={pending}
        aria-describedby={hint || error ? statusId : undefined}
        disabled={pending || state === 'unsupported' || state === 'blocked'}
        title={
          hint ||
          'Desktop alerts for completed turns and questions. Project and conversation titles are visible; messages are not included.'
        }
        onClick={toggle}
        className="border-border-subtle text-ink-muted focus-visible:outline-accent rounded border px-2 py-1 text-xs focus-visible:outline-2 disabled:opacity-50"
      >
        {pending
          ? 'Updating alerts…'
          : enabled
            ? 'Alerts on'
            : state === 'blocked'
              ? 'Alerts blocked'
              : state === 'unsupported'
                ? 'Alerts unavailable'
                : 'Enable alerts'}
      </button>
      {hint || error ? (
        <p
          id={statusId}
          role="status"
          className={
            error
              ? 'border-border-subtle bg-surface-raised text-ink-muted absolute top-full right-0 z-20 mt-2 w-56 rounded border p-2 text-xs shadow-sm'
              : 'sr-only'
          }
        >
          {hint || error}
          {error ? (
            <button type="button" onClick={() => setError('')} className="text-ink mt-2 block underline">
              Dismiss
            </button>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}
