import { pythonTokens } from './codePresentation';

/** Present source as React text nodes, not HTML generated from tool arguments. */
export function PythonCode({ code }: { code: string }) {
  return (
    <pre className="tool-code scroll-thin">
      <code>
        {pythonTokens(code).map((token, index) => (
          <span key={index} className={token.kind ? `syntax-${token.kind}` : undefined}>
            {token.text}
          </span>
        ))}
      </code>
    </pre>
  );
}
