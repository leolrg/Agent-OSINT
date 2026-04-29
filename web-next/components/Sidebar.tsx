import Link from 'next/link';
import { ScanList } from './ScanList';
import type { ScanRow } from './ScanList';

export function Sidebar({
  scans, currentScanId,
}: { scans: ScanRow[]; currentScanId?: string }) {
  return (
    <aside className="w-[140px] bg-white border-r-[3px] border-ink p-3 shrink-0 min-h-screen">
      <div className="text-[10px] font-extrabold tracking-[0.16em] mb-3.5">
        A-OSINT
      </div>
      <Link
        href="/scans/new"
        className="block bg-ink text-white py-1.5 px-2 text-[9px] font-bold tracking-[0.1em] uppercase text-center mb-3.5"
      >
        + NEW
      </Link>
      <ScanList scans={scans} currentScanId={currentScanId} />
    </aside>
  );
}
