/**
 * The shapes the server sends. They mirror `src/server/views.py`, which is the one
 * place that decides them: this file is a copy for the type checker, not a second
 * opinion. A field the server stops sending should fail here, not at runtime.
 */

export type Attachment = {
  kind: 'image' | 'binary';
  media_type: string;
  width: number;
  height: number;
  bytes: number;
  size: string;
  label: string;
  identifier: string | null;
};

export type TranscriptItem = {
  kind: 'user' | 'assistant' | 'thinking' | 'tool_call' | 'tool_result' | 'subagent_report';
  index: number;
  text?: string;
  /** Present on a user message that carried pictures; never the payload itself. */
  attachments?: Attachment[];
  tool_name?: string;
  call_id?: string;
  args?: unknown;
  ok?: boolean;
  summary?: { text: string; status: 'good' | 'bad' | 'neutral' } | null;
  /** Present on a report: the execution that sent it, and what to call it. */
  node_id?: string;
  label?: string;
  short_id?: string;
};

/**
 * One window of the transcript: the items, the absolute position the page starts at,
 * and whether anything older exists. `first_index` doubles as the `before` cursor for
 * the page above it, and `pending` arrives on the newest page only — a row waiting in
 * the queue is not history and has no place in a chunk.
 */
export type TranscriptPage = {
  session: SessionView;
  items: TranscriptItem[];
  first_index: number;
  has_more: boolean;
  total: number;
  skills: string[];
  pending: DurableEntry[];
};

export type SessionView = {
  id: string;
  short_id: string;
  title: string | null;
  display_title: string;
  workspace: string;
  agent: string | null;
  created_at: string;
  updated_at: string;
  messages: number;
};

export type SessionSummary = {
  id: string;
  short_id: string;
  title: string | null;
  display_title: string;
  preview: string;
  workspace: string;
  profile: string | null;
  updated_at: string;
  workspace_label: string;
  is_current: boolean;
  repository: string | null;
  is_worktree: boolean;
};

export type WorkspaceView = {
  root: string;
  label: string;
  branch: string | null;
  is_worktree: boolean;
  is_main: boolean;
  is_detached: boolean;
  is_locked: boolean;
  repository: string | null;
  active?: boolean;
};

/** One repository and every workspace of it: the main checkout, then its worktrees. */
export type RepositoryGroup = {
  name: string;
  root: string;
  workspaces: WorkspaceView[];
};

export type NodeView = {
  id: string;
  short_id: string;
  name: string;
  label: string;
  role: string;
  status: string;
  parent_id: string | null;
  depth: number;
  task: string;
  result: string;
  error: string;
  model: string;
  runs: number;
  elapsed: number;
};

export type RoadmapItem = {
  id: string;
  title: string;
  status: string;
  notes: string | null;
};

export type ModelView = {
  configured: boolean;
  account: { id: string; alias: string; kind: string } | null;
  model: string;
  thinking: string;
  label: string;
  window: number | null;
  notice: string | null;
};

export type AgentView = {
  name: string;
  description: string;
  role: string;
  source: string;
  max_steps: number | null;
  active: boolean;
};

export type QuestionOption = { label: string; description: string | null; recommended: boolean };

export type QuestionItem = {
  question: string;
  allow_custom: boolean;
  options: QuestionOption[];
};

export type PendingQuestion = { id: string; questions: QuestionItem[] };

export type State = {
  session: SessionView;
  workspace: WorkspaceView;
  model: ModelView;
  agent: AgentView | null;
  agents: AgentView[];
  tree: { root_id: string | null; nodes: NodeView[] };
  roadmap: RoadmapItem[];
  busy: boolean;
  question: PendingQuestion | null;
  mcp: { servers: unknown[]; loading?: boolean; errors?: string[] };
  skills: string[];
  seq: number;
};

export type LoginState = {
  authenticated: boolean;
  version: string;
  bundle: { stale: boolean; reason: string };
};

export type DurableEntry = {
  request_id: string;
  sequence: number;
  kind: 'turn' | 'steer';
  state: 'accepted' | 'claimed' | 'delivered' | 'finished' | 'stopped' | 'interrupted';
  text: string;
  image_count: number;
  delivered: boolean;
  checkpointed: boolean | null;
  destination_id: string | null;
  destination_available: boolean;
};

export type DurableSession = {
  session: { id: string; title: string | null; updated_at: string; preview: string };
  transcript: TranscriptItem[];
  skills: string[];
  queue: DurableEntry[];
  pending: DurableEntry[];
  deliveries: {
    request_id: string;
    checkpointed: boolean;
    destination_id: string | null;
    destination_available: boolean;
  }[];
  recoveries: { recovery_session_id: string; available: boolean }[];
  conflicted_entries: DurableEntry[];
};
