import type { GoalView } from './types';

/**
 * How a goal reads in the sidebar.
 *
 * The marks and tones are the roadmap's own, so the goal and the plan under it
 * are one block: `▸` is running, `✓` is done, `!` is blocked. The one difference
 * is the word "yolo", which is spelled out rather than left to a colour: a mode
 * that removes questions is not something a reader should have to infer.
 */
const MARKS: Record<string, string> = {
  armed: '○',
  active: '▸',
  waiting: '○',
  blocked: '!',
  completed: '✓',
  stopped: '·',
};

const FALLBACK_MARK = '·';

export type GoalPresentation = {
  mark: string;
  tone: string;
  text: string;
  mode: string | null;
};

function goalTone(status: string): string {
  if (status === 'completed') return 'text-accent';
  if (status === 'blocked') return 'text-warn';
  if (status === 'active') return 'text-ink';
  return 'text-ink-muted';
}

export function presentGoal(goal: GoalView): GoalPresentation {
  return {
    mark: MARKS[goal.status] ?? FALLBACK_MARK,
    tone: goalTone(goal.status),
    text: goal.text,
    mode: goal.yolo ? 'yolo' : null,
  };
}
