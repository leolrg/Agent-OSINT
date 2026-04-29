import { auth } from '../../../auth';
import { redirect, notFound } from 'next/navigation';
import { db } from '../../../lib/db';
import { scans } from '../../../drizzle/schema';
import { eq } from 'drizzle-orm';
import { ProgressStream } from '../../../components/ProgressStream';

type Params = Promise<{ id: string }>;

export default async function ScanDetailPage({ params }: { params: Params }) {
  const { id } = await params;
  const session = await auth();
  if (!session?.user?.id) redirect('/auth/signin');

  const rows = await db.select().from(scans).where(eq(scans.id, id)).limit(1);
  const sc = rows[0];
  if (!sc || sc.userId !== session.user.id) notFound();

  const subject = (sc.params as { subject?: string })?.subject ?? '(no subject)';
  const goal = (sc.params as { goal?: string })?.goal;
  const shortId = id.slice(0, 8);

  return (
    <div>
      <div className="text-[10px] font-bold tracking-[0.1em] uppercase">
        SCAN · {shortId}
      </div>
      <h1 className="text-[18px] font-extrabold leading-[1.1]">
        {subject.toUpperCase()}
      </h1>
      {goal && <p className="text-[11px] text-muted mt-0.5">{goal}</p>}

      <ProgressStream
        scanId={id}
        initialStatus={sc.status as 'queued' | 'running' | 'completed' | 'failed'}
        startedAt={sc.startedAt?.toISOString() ?? null}
      />

      {(sc.status === 'queued' || sc.status === 'running') && (
        <div className="mt-4.5 p-10 px-5 border-2 border-dashed border-dashed text-center min-h-[140px] flex flex-col justify-center items-center">
          <div className="label-uppercase text-muted2">REPORT</div>
          <div className="text-[13px] text-muted2 mt-1.5">
            Will appear when investigation completes…
          </div>
        </div>
      )}
    </div>
  );
}
