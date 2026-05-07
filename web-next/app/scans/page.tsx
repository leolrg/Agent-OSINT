import Link from 'next/link';
import { auth } from '../../auth';
import { redirect } from 'next/navigation';
import { db } from '../../lib/db';
import { scans } from '../../drizzle/schema';
import { desc, eq } from 'drizzle-orm';

export default async function ScansIndexPage() {
  const session = await auth();
  if (!session?.user?.id) redirect('/auth/signin');

  const latest = await db.select({ id: scans.id })
    .from(scans).where(eq(scans.userId, session.user.id))
    .orderBy(desc(scans.createdAt)).limit(1);

  if (latest.length > 0) {
    redirect(`/scans/${latest[0].id}`);
  }

  // Empty state
  return (
    <div className="flex items-start pt-12 pl-6 max-w-[420px]">
      <div>
        <div className="label-uppercase text-muted2">WELCOME</div>
        <h1 className="text-[24px] font-extrabold leading-[1.05] heavy-rule pb-2.5 mt-1">
          Run your first scan.
        </h1>
        <p className="mt-3.5 text-[13px] text-muted leading-[1.5]">
          Pick an agent, name the subject, and watch the investigation happen
          live. First scan typically runs in under 10 minutes and costs less
          than a dollar.
        </p>
        <Link
          href="/scans/new"
          className="inline-block bg-ink text-white py-2.5 px-4 text-[11px] font-bold tracking-[0.12em] uppercase mt-4.5"
        >
          + NEW SCAN
        </Link>
      </div>
    </div>
  );
}
