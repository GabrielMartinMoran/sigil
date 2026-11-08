const ENABLED_KEY = 'sigil.desktop-notifications';
const SETTINGS_EVENT = 'sigil:notification-settings';
let activeNotice: Notification | null = null;

export type NotificationState = 'enabled' | 'disabled' | 'blocked' | 'unsupported';

export type NotificationContext = { project: string; conversation: string };

/** The event body remains generic; the title identifies the project and conversation. */
export function attentionMessage(type: string, data: Record<string, unknown>): string | null {
  if (type === 'question') return 'A question is waiting for your answer.';
  if (type !== 'turn_finished') return null;
  if (data.status === 'ok') return 'Turn finished. Your response is ready.';
  if (data.status === 'error') return 'Turn failed. Open Sigil for details.';
  return null;
}

export function notificationPermission(): NotificationPermission | 'unsupported' {
  return typeof window !== 'undefined' && window.isSecureContext && typeof Notification !== 'undefined'
    ? Notification.permission
    : 'unsupported';
}

export function notificationsEnabled(): boolean {
  try {
    return notificationPermission() === 'granted' && window.localStorage.getItem(ENABLED_KEY) === 'true';
  } catch {
    return false;
  }
}

export function notificationState(): NotificationState {
  const permission = notificationPermission();
  if (permission === 'unsupported') return 'unsupported';
  if (permission === 'denied') return 'blocked';
  return notificationsEnabled() ? 'enabled' : 'disabled';
}

/** Browser permissions and preferences can change outside this React tree. */
export function subscribeNotifications(onChange: () => void): () => void {
  const refresh = () => {
    if (!notificationsEnabled()) closeAttention();
    onChange();
  };
  const focus = () => {
    closeAttention();
    refresh();
  };
  window.addEventListener('focus', focus);
  window.addEventListener('storage', refresh);
  window.addEventListener(SETTINGS_EVENT, refresh);
  const unwatchPermission = watchPermission(refresh);
  return () => {
    window.removeEventListener('focus', focus);
    window.removeEventListener('storage', refresh);
    window.removeEventListener(SETTINGS_EVENT, refresh);
    unwatchPermission();
  };
}

function watchPermission(onChange: () => void): () => void {
  let stopped = false;
  let status: PermissionStatus | undefined;
  if (typeof navigator !== 'undefined' && navigator.permissions) {
    void navigator.permissions
      .query({ name: 'notifications' })
      .then((result) => {
        if (stopped) return;
        status = result;
        status.addEventListener('change', onChange);
        onChange();
      })
      .catch(() => undefined);
  }
  return () => {
    stopped = true;
    status?.removeEventListener('change', onChange);
  };
}

/** Called from an explicit button gesture, never from a server event or startup. */
export async function setNotificationsEnabled(enabled: boolean): Promise<boolean> {
  try {
    let permission = notificationPermission();
    if (enabled && permission === 'default') permission = await Notification.requestPermission();
    const allowed = enabled && permission === 'granted';
    if (!allowed) closeAttention();
    window.localStorage.setItem(ENABLED_KEY, String(allowed));
    window.dispatchEvent(new Event(SETTINGS_EVENT));
    return allowed;
  } catch {
    return false;
  }
}

export function closeAttention(): void {
  const notice = activeNotice;
  activeNotice = null;
  try {
    notice?.close();
  } catch {
    // Closing an unavailable desktop surface must not interrupt the app either.
  }
}

export function showAttention(type: string, data: Record<string, unknown>, context?: NotificationContext): void {
  if (['question_closed', 'turn_started', 'turn_finished'].includes(type)) closeAttention();
  const body = attentionMessage(type, data);
  if (!body || !notificationsEnabled()) return;
  closeAttention();
  try {
    const title =
      [context?.project, context?.conversation]
        .map((part) =>
          Array.from(part ?? '', (character) => {
            const code = character.charCodeAt(0);
            return code < 32 || code === 127 ? ' ' : character;
          })
            .join('')
            .trim(),
        )
        .filter(Boolean)
        .join(' — ')
        .slice(0, 120) || 'Sigil';
    const notice = new Notification(title, { body, tag: 'sigil-attention' });
    activeNotice = notice;
    notice.onclick = () => {
      window.focus();
      closeAttention();
    };
  } catch {
    // Desktop APIs can be unavailable even after permission, notably on mobile.
  }
}

/** A fresh page ignores historical replay; reconnects retain the high-water mark. */
export function createAttentionListener(
  afterSeq: number,
  context?: () => NotificationContext | undefined,
): (type: string, data: Record<string, unknown>) => void {
  let lastSeq = afterSeq;
  return (type, data) => {
    if (typeof data.seq !== 'number' || !Number.isSafeInteger(data.seq) || data.seq <= lastSeq) return;
    lastSeq = data.seq;
    showAttention(type, data, context?.());
  };
}
