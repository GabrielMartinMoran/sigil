import { useEffect, useState } from 'react';

import { useStore } from './store';

type Tab = 'sessions' | 'workspaces' | 'plan';

/**
 * The side panel: conversations, workspaces and the plan.
 *
 * On a narrow screen it is a drawer: the header has the toggle and the panel covers
 * the transcript instead of squeezing it, because a 320-pixel-wide chat column is not
 * a chat. On `md` and up it is a column and the toggle disappears.
 */
export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('sessions');
  const sessions = useStore((store) => store.sessions);
  const repositories = useStore((store) => store.repositories);
  const state = useStore((store) => store.state);
  const current = state?.session.id;
  const openSession = useStore((store) => store.openSession);
  const newSession = useStore((store) => store.newSession);
  const switchWorkspace = useStore((store) => store.switchWorkspace);
  const loadSessions = useStore((store) => store.loadSessions);
  const loadWorkspaces = useStore((store) => store.loadWorkspaces);
  const busy = useStore((store) => store.busy);

  useEffect(() => {
    if (!open) return;
    void loadSessions();
    void loadWorkspaces();
  }, [open, loadSessions, loadWorkspaces]);

  const plan = state?.roadmap ?? [];
  const nodes = state?.tree.nodes ?? [];

  return (
    <>
      {open ? (
        <button
          type="button"
          aria-label="Close the panel"
          onClick={onClose}
          className="fixed inset-0 z-20 bg-black/50 md:hidden"
        />
      ) : null}

      <aside
        className={`border-border-subtle bg-surface-raised fixed inset-y-0 left-0 z-30 flex w-[min(20rem,85vw)] flex-col border-r transition-transform md:static md:z-0 md:w-72 md:translate-x-0 ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="border-border-subtle flex items-center gap-1 border-b p-2">
          {(['sessions', 'workspaces', 'plan'] as const).map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => setTab(name)}
              className={`flex-1 rounded px-2 py-1.5 text-xs font-medium capitalize ${
                tab === name ? 'bg-surface-sunken text-ink' : 'text-ink-faint hover:text-ink-muted'
              }`}
            >
              {name}
            </button>
          ))}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="text-ink-faint hover:text-ink rounded px-2 py-1.5 md:hidden"
          >
            ✕
          </button>
        </div>

        <div className="scroll-thin flex-1 overflow-y-auto p-2">
          {tab === 'sessions' ? (
            <>
              <button
                type="button"
                onClick={() => void newSession()}
                disabled={busy}
                className="border-border-subtle text-ink-muted hover:border-accent mb-2 w-full rounded border px-2 py-1.5 text-left text-xs disabled:opacity-40"
              >
                + new session
              </button>
              {sessions.length === 0 ? <p className="text-ink-faint px-1 py-4 text-xs">No conversations yet.</p> : null}
              <ul className="space-y-0.5">
                {sessions.map((session) => (
                  <li key={session.id}>
                    <button
                      type="button"
                      onClick={() => void openSession(session.id)}
                      disabled={busy}
                      className={`w-full rounded px-2 py-1.5 text-left text-xs disabled:opacity-40 ${
                        session.id === current ? 'bg-surface-sunken text-ink' : 'text-ink-muted hover:bg-surface-sunken'
                      }`}
                    >
                      <span className="block truncate">{session.display_title || session.preview || 'untitled'}</span>
                      <span className="text-ink-faint mt-0.5 block truncate font-mono text-[10px]">
                        {where(session)} · {session.updated_at.slice(0, 16).replace('T', ' ')}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          {tab === 'workspaces' ? (
            <div className="space-y-3">
              {repositories.map((repository) => (
                <section key={repository.root}>
                  {/* The repository is the header, not one more row: eleven worktrees of
                      one repo are one project, and a flat list made them look like
                      eleven unrelated directories. */}
                  <h2 className="flex items-baseline gap-1.5 px-1 pb-1">
                    <span className="text-ink truncate font-mono text-[11px] font-semibold">{repository.name}</span>
                    <span className="text-ink-faint shrink-0 font-mono text-[10px]">
                      {repository.workspaces.length === 1
                        ? 'no worktrees'
                        : `${repository.workspaces.length - 1} worktree${repository.workspaces.length === 2 ? '' : 's'}`}
                    </span>
                  </h2>
                  <ul className="space-y-0.5">
                    {repository.workspaces.map((workspace) => (
                      <li key={workspace.root}>
                        <button
                          type="button"
                          onClick={() => void switchWorkspace(workspace.root)}
                          disabled={busy || workspace.active === true}
                          title={workspace.root}
                          className={`w-full rounded px-2 py-1.5 text-left text-xs disabled:opacity-60 ${
                            workspace.active === true
                              ? 'bg-surface-sunken text-accent'
                              : 'text-ink-muted hover:bg-surface-sunken'
                          }`}
                        >
                          <span className="flex items-baseline gap-1.5">
                            <span className="truncate">{workspace.is_main ? 'main checkout' : workspace.label}</span>
                            {workspace.is_worktree ? (
                              <span className="border-border-subtle text-ink-faint shrink-0 rounded border px-1 font-mono text-[9px] tracking-wide uppercase">
                                worktree
                              </span>
                            ) : null}
                            {workspace.is_locked ? (
                              <span className="text-warn shrink-0 font-mono text-[9px]">locked</span>
                            ) : null}
                          </span>
                          <span className="text-ink-faint mt-0.5 block truncate font-mono text-[10px]">
                            {workspace.is_detached ? 'detached HEAD' : workspace.branch}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          ) : null}

          {tab === 'plan' ? (
            <div className="space-y-4">
              <section>
                <h2 className="text-ink-faint px-1 pb-1 font-mono text-[10px] tracking-wide uppercase">roadmap</h2>
                {plan.length === 0 ? (
                  <p className="text-ink-faint px-1 py-2 text-xs">No plan for this execution.</p>
                ) : (
                  <ul className="space-y-1">
                    {plan.map((item) => (
                      <li key={item.id} className="px-1 text-xs">
                        <span className="text-ink-faint mr-1.5 font-mono text-[10px]">
                          {item.status === 'done' ? '✓' : item.status === 'in_progress' ? '▸' : '·'}
                        </span>
                        <span className={item.status === 'done' ? 'text-ink-faint line-through' : 'text-ink-muted'}>
                          {item.title}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section>
                <h2 className="text-ink-faint px-1 pb-1 font-mono text-[10px] tracking-wide uppercase">agents</h2>
                {nodes.length === 0 ? (
                  <p className="text-ink-faint px-1 py-2 text-xs">Nobody is running.</p>
                ) : (
                  <ul className="space-y-1">
                    {nodes.map((node) => (
                      <li key={node.id} className="px-1 text-xs" style={{ paddingLeft: `${4 + node.depth * 10}px` }}>
                        <span className="text-ink-faint mr-1 font-mono text-[10px]">
                          {node.status === 'running' ? '▸' : node.status === 'done' ? '✓' : '·'}
                        </span>
                        <span className="text-ink-muted">{node.label || node.name}</span>
                        <span className="text-ink-faint ml-1 font-mono text-[10px]">{node.elapsed}s</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>
          ) : null}
        </div>

        <Footer />
      </aside>
    </>
  );
}

function Footer() {
  const state = useStore((store) => store.state);
  const logout = useStore((store) => store.logout);
  if (state === null) return null;
  const { model, workspace } = state;
  return (
    <div className="border-border-subtle text-ink-faint border-t p-2 font-mono text-[10px] leading-relaxed">
      <div className="truncate" title={workspace.root}>
        {whereWorkspace(workspace)}
        {workspace.is_worktree ? <span className="ml-1 opacity-70">(worktree)</span> : null}
      </div>
      <div className="truncate" title={model.label}>
        {model.configured ? model.label : 'no model configured'}
        {model.thinking !== 'default' ? ` · ${model.thinking}` : ''}
      </div>
      <button type="button" onClick={() => void logout()} className="text-ink-faint hover:text-danger mt-1">
        sign out
      </button>
    </div>
  );
}

/**
 * Where a conversation lives, in one line: `sigil › better-debug`.
 *
 * The repository first, because eleven worktrees of one repo are one project with
 * eleven checkouts, not eleven projects. The separator is a chevron rather than a
 * slash on purpose: this is a containment, not a path.
 */
function where(session: { repository: string | null; workspace_label: string; is_worktree: boolean }): string {
  if (!session.repository) return session.workspace_label;
  return session.is_worktree ? `${session.repository} › ${session.workspace_label}` : `${session.repository} › main`;
}

function whereWorkspace(workspace: { repository: string | null; label: string; is_worktree: boolean }): string {
  if (!workspace.repository) return workspace.label;
  return workspace.is_worktree ? `${workspace.repository} › ${workspace.label}` : `${workspace.repository} › main`;
}
