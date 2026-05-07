import Link from 'next/link';

export type ScanRow = {
  id: string;
  subject: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  totalCostUsd: number | null;
};

export function ScanList({
  scans, currentScanId,
}: { scans: ScanRow[]; currentScanId?: string }) {
  if (scans.length === 0) {
    return (
      <div className="text-[11px] text-muted2 italic px-1.5 py-1">
        No scans yet
      </div>
    );
  }
  const running = scans.filter((s) => s.status === 'running' || s.status === 'queued');
  const done = scans.filter((s) => s.status === 'completed' || s.status === 'failed');

  return (
    <div className="space-y-3">
      {running.length > 0 && (
        <div>
          <div className="text-[9px] font-bold tracking-[0.1em] uppercase text-muted2 mb-1">
            Running
          </div>
          {running.map((s) => (
            <ScanRowItem key={s.id} row={s} active={s.id === currentScanId} />
          ))}
        </div>
      )}
      {done.length > 0 && (
        <div>
          <div className="text-[9px] font-bold tracking-[0.1em] uppercase text-muted2 mb-1">
            Done
          </div>
          {done.map((s) => (
            <ScanRowItem key={s.id} row={s} active={s.id === currentScanId} />
          ))}
        </div>
      )}
    </div>
  );
}

function ScanRowItem({ row, active }: { row: ScanRow; active: boolean }) {
  const cost = row.totalCostUsd ? `$${row.totalCostUsd.toFixed(2)}` : '';
  const tag =
    row.status === 'running' ? 'running'
    : row.status === 'queued' ? 'queued'
    : row.status === 'failed' ? 'failed'
    : `done · ${cost}`;
  const tagColor =
    row.status === 'running' || row.status === 'queued' ? 'text-accent'
    : row.status === 'failed' ? 'text-danger'
    : 'text-muted';
  return (
    <Link
      href={`/scans/${row.id}`}
      className={
        'block px-1.5 py-1 mb-0.5 text-[11px] '
        + (active ? 'bg-sidebar border-l-2 border-ink font-semibold' : 'text-muted')
      }
    >
      <div className="truncate">{row.subject}</div>
      <div className={`text-[9px] mt-0.5 ${tagColor}`}>{tag}</div>
    </Link>
  );
}
