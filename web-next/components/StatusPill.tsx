type Props = {
  active?: { displayLabel: string; argSummary: string };
  elapsedSec: number;
  costUsd?: number;
  sourcesCount?: number;
};

export function StatusPill({ active, elapsedSec, costUsd, sourcesCount }: Props) {
  const time = `${Math.floor(elapsedSec / 60)}:${String(elapsedSec % 60).padStart(2, '0')}`;
  return (
    <div className="bg-ink text-white p-2.5 px-3.5 mt-3.5">
      <div className="flex items-center gap-2.5">
        <div
          className="w-2 h-2 bg-spotlight rounded-full"
          style={{ animation: 'pulse 1.2s infinite' }}
        />
        <div className="text-[11px] font-bold tracking-[0.1em] uppercase text-muted2">
          {active?.displayLabel ?? 'Starting…'}
        </div>
        <div className="ml-auto text-[10px] text-muted2 tracking-[0.1em]">
          {sourcesCount !== undefined && `${sourcesCount} SRC · `}
          {time}
          {costUsd !== undefined && ` · $${costUsd.toFixed(2)}`}
        </div>
      </div>
      {active?.argSummary && (
        <div className="text-[13px] font-semibold mt-1 font-mono">
          {active.argSummary}
        </div>
      )}
      <style jsx>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
    </div>
  );
}
