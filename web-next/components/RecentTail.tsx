export type TailItem = {
  ts: number;
  displayLabel: string;
  argSummary: string;
  resultSummary: string;
};

export function RecentTail({ items }: { items: TailItem[] }) {
  if (items.length === 0) return null;
  const visible = items.slice(0, 3);
  return (
    <div className="mt-2 px-1">
      {visible.map((it, idx) => {
        const opacity = idx === 0 ? 1 : idx === 1 ? 0.75 : 0.5;
        const sec = Math.floor(it.ts);
        return (
          <div
            key={`${it.ts}-${idx}`}
            className="flex gap-2.5 py-1 font-mono text-[11px] text-muted"
            style={{ opacity }}
          >
            <div className="text-muted2 min-w-[44px]">+{sec}s</div>
            <div className="flex-1 min-w-0 truncate">
              <strong className="text-ink">{it.displayLabel}</strong>{' '}
              {it.argSummary}{' '}
              {it.resultSummary && <span className="text-muted2">→ {it.resultSummary}</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
