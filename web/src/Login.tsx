import { useState, type FormEvent } from 'react';

import { useStore } from './store';
import type { LoginState } from './types';

/**
 * The password gate.
 *
 * It is a real form so the browser offers "save password" and the Enter key works
 * without a handler of our own; the error line is the server's sentence, not a
 * rewritten one — a "wrong password" and a "too many attempts" are different
 * situations and the person at the keyboard is the one who knows which.
 */
export function Login({ status }: { status: LoginState | null }) {
  const login = useStore((store) => store.login);
  const error = useStore((store) => store.error);
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!password || pending) return;
    setPending(true);
    void login(password).finally(() => setPending(false));
  };

  return (
    <main className="flex min-h-full items-center justify-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm">
        <h1 className="text-ink font-mono text-2xl tracking-tight">sigil</h1>
        <p className="text-ink-muted mt-1 text-sm">The password is printed in the terminal that started the server.</p>

        <label className="text-ink-faint mt-6 block text-xs font-medium tracking-wide uppercase" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          autoFocus
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="border-border-subtle bg-surface-sunken text-ink focus:border-accent mt-2 w-full rounded-md border px-3 py-2 font-mono text-sm outline-none"
        />

        <button
          type="submit"
          disabled={pending || !password}
          className="bg-accent text-accent-ink mt-4 w-full rounded-md px-3 py-2 text-sm font-semibold disabled:opacity-40"
        >
          {pending ? 'Signing in…' : 'Sign in'}
        </button>

        {error ? <p className="text-danger mt-4 text-sm">{error}</p> : null}

        {status?.bundle.stale ? (
          <p className="border-border-subtle text-warn mt-6 border-t pt-4 font-mono text-xs leading-relaxed">
            {status.bundle.reason}
            <br />
            Run `npm run build` in web/.
          </p>
        ) : null}
      </form>
    </main>
  );
}
