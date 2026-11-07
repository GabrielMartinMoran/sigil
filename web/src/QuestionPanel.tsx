import { useState } from 'react';

import { useStore } from './store';
import type { PendingQuestion } from './types';

/**
 * The agent's questions, where the composer is.
 *
 * Same rule as the terminal client: this is not a modal. It replaces the composer
 * because the composer is where the user acts, and taking the whole screen away to
 * ask about what is on it would hide the conversation the question refers to.
 *
 * "Other" is always offered when the question allows it, because the three options a
 * model writes are rarely the whole set of answers.
 */
export function QuestionPanel({ question }: { question: PendingQuestion }) {
  const answer = useStore((store) => store.answer);
  const [custom, setCustom] = useState<Record<number, string>>({});

  const respond = (index: number, value: string, isCustom: boolean) => {
    const current = question.questions[index];
    if (current === undefined) return;
    const answers = question.questions.map((item, position) => ({
      question: item.question,
      answer: position === index ? value : '',
      custom: position === index ? isCustom : false,
    }));
    // One question at a time: the others travel empty, which the tool reads as
    // "the user answered this one". A batch is asked and answered together.
    void answer(
      question.id,
      answers.filter((item) => item.answer !== ''),
    );
  };

  return (
    <div className="border-border-subtle bg-surface-raised border-t px-3 py-4 sm:px-4">
      <div className="mx-auto max-w-3xl space-y-4">
        {question.questions.map((item, index) => (
          <div key={item.question}>
            <p className="text-ink text-sm font-medium">{item.question}</p>
            <div className="mt-2 space-y-1.5">
              {item.options.map((option) => (
                <button
                  key={option.label}
                  type="button"
                  onClick={() => respond(index, option.label, false)}
                  className="border-border-subtle bg-surface-sunken text-ink hover:border-accent block w-full rounded-md border px-3 py-2 text-left text-sm"
                >
                  <span className="font-medium">{option.label}</span>
                  {option.recommended ? (
                    <span className="text-accent ml-2 font-mono text-[11px]">recommended</span>
                  ) : null}
                  {option.description ? (
                    <span className="text-ink-muted mt-0.5 block text-xs">{option.description}</span>
                  ) : null}
                </button>
              ))}
            </div>

            {item.allow_custom ? (
              <form
                className="mt-2 flex gap-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  const value = (custom[index] ?? '').trim();
                  if (value) respond(index, value, true);
                }}
              >
                <input
                  value={custom[index] ?? ''}
                  onChange={(event) => setCustom({ ...custom, [index]: event.target.value })}
                  placeholder="Something else…"
                  className="border-border-subtle bg-surface-sunken text-ink placeholder:text-ink-faint focus:border-accent flex-1 rounded-md border px-3 py-2 text-sm outline-none"
                />
                <button type="submit" className="border-border-subtle text-ink-muted rounded-md border px-3 text-sm">
                  Answer
                </button>
              </form>
            ) : null}
          </div>
        ))}

        <button
          type="button"
          onClick={() => void answer(question.id, null)}
          className="text-ink-faint hover:text-danger font-mono text-xs"
        >
          cancel this question
        </button>
      </div>
    </div>
  );
}
