import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export function ReportMarkdown({ text }: { text: string }) {
  return (
    <div className="text-[13px] leading-[1.55] text-[#1f1f1f]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h1 className="text-[16px] font-extrabold tracking-[0.04em] uppercase mt-3.5 mb-1.5 pb-1 heavy-rule">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-[14px] font-extrabold tracking-[0.04em] uppercase mt-3.5 mb-1.5 pb-1 heavy-rule">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-[13px] font-bold tracking-[0.04em] uppercase mt-3 mb-1">
              {children}
            </h3>
          ),
          p: ({ children }) => <p className="my-2.5">{children}</p>,
          ul: ({ children }) => <ul className="my-2 pl-[18px] list-disc">{children}</ul>,
          ol: ({ children }) => <ol className="my-2 pl-[18px] list-decimal">{children}</ol>,
          code: ({ children }) => (
            <code className="text-[11px] bg-sidebar px-1 py-0.5 font-mono">{children}</code>
          ),
          a: ({ children, href }) => (
            <a href={href} className="text-ink underline" target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
