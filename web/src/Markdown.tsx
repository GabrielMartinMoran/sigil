import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/**
 * Model output, rendered as Markdown.
 *
 * The terminal client renders it with Rich `Markdown` and for the same reason: the
 * model writes Markdown, and showing the asterisks is throwing away most of the
 * answer's structure — a table becomes unreadable, a code block loses its shape.
 *
 * **`react-markdown` and not a string-to-HTML parser.** It builds React elements, so
 * there is no `innerHTML` and raw HTML in the model's output is dropped rather than
 * executed: a model cannot inject a script into this page, and neither can a web page
 * it quoted. That property is the reason for the dependency, not its size.
 *
 * Styling is per-element instead of a `prose` plugin because there is one surface to
 * style and a design system of one file. Everything here derives from the same
 * tokens the rest of the client uses.
 */
export function MarkdownText({ text }: { text: string }) {
  return (
    <div className="text-ink text-sm leading-relaxed">
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
          h1: ({ children }) => <h1 className="text-ink mt-5 mb-2 text-lg font-semibold first:mt-0">{children}</h1>,
          h2: ({ children }) => <h2 className="text-ink mt-4 mb-2 text-base font-semibold first:mt-0">{children}</h2>,
          h3: ({ children }) => <h3 className="text-ink mt-3 mb-1.5 text-sm font-semibold first:mt-0">{children}</h3>,
          h4: ({ children }) => (
            <h4 className="text-ink-muted mt-3 mb-1 text-sm font-semibold first:mt-0">{children}</h4>
          ),
          ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
          li: ({ children }) => <li className="marker:text-ink-faint pl-0.5">{children}</li>,
          blockquote: ({ children }) => (
            <blockquote className="border-border-subtle text-ink-muted my-2 border-l-2 pl-3 italic">
              {children}
            </blockquote>
          ),
          hr: () => <hr className="border-border-subtle my-4" />,
          strong: ({ children }) => <strong className="text-ink font-semibold">{children}</strong>,
          em: ({ children }) => <em className="italic">{children}</em>,
          del: ({ children }) => <del className="text-ink-faint line-through">{children}</del>,
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              // `noopener` matters even for a link the model wrote: the opened page
              // must not get a handle on this window.
              rel="noopener noreferrer"
              className="text-accent underline decoration-dotted underline-offset-2 hover:decoration-solid"
            >
              {children}
            </a>
          ),
          // A fenced block: `pre` owns the box, `code` inside it is plain monospace.
          pre: ({ children }) => (
            <pre className="scroll-thin border-border-subtle bg-surface-sunken my-2 overflow-x-auto rounded border p-3 font-mono text-xs leading-relaxed">
              {children}
            </pre>
          ),
          code: ({ className, children }) => {
            const fenced = typeof className === 'string' && className.includes('language-');
            if (fenced) return <code className="text-ink">{children}</code>;
            return (
              <code className="border-border-subtle bg-surface-sunken text-ink rounded border px-1 py-0.5 font-mono text-[0.85em]">
                {children}
              </code>
            );
          },
          table: ({ children }) => (
            <div className="scroll-thin my-2 overflow-x-auto">
              <table className="w-full border-collapse text-left text-xs">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="border-border-subtle border-b">{children}</thead>,
          th: ({ children }) => <th className="text-ink px-2 py-1.5 font-semibold">{children}</th>,
          td: ({ children }) => (
            <td className="border-border-subtle text-ink-muted border-t px-2 py-1.5">{children}</td>
          ),
        }}
      >
        {text}
      </Markdown>
    </div>
  );
}
