import { auth } from '../../auth';
import { redirect } from 'next/navigation';
import { db } from '../../lib/db';
import { scans } from '../../drizzle/schema';
import { desc, eq } from 'drizzle-orm';
import { Sidebar } from '../../components/Sidebar';
import type { ScanRow } from '../../components/ScanList';

export default async function ScansLayout({
  children,
}: { children: React.ReactNode }) {
  const session = await auth();
  if (!session?.user?.id) redirect('/auth/signin');

  const rows = await db.select({
    id: scans.id, subject: scans.params, status: scans.status,
    totalCostUsd: scans.totalCostUsd,
  })
    .from(scans)
    .where(eq(scans.userId, session.user.id))
    .orderBy(desc(scans.createdAt))
    .limit(50);

  const scanRows: ScanRow[] = rows.map((r) => ({
    id: r.id,
    subject: (r.subject as { subject?: string })?.subject ?? '(no subject)',
    status: r.status as ScanRow['status'],
    totalCostUsd: r.totalCostUsd ? Number(r.totalCostUsd) : null,
  }));

  return (
    <div className="min-h-screen flex">
      <Sidebar scans={scanRows} />
      <main className="flex-1 p-4">{children}</main>
    </div>
  );
}
