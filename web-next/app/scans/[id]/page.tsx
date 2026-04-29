import { auth } from '../../../auth';
import { redirect, notFound } from 'next/navigation';
import { db } from '../../../lib/db';
import { scans } from '../../../drizzle/schema';
import { eq } from 'drizzle-orm';
import { S3Client, GetObjectCommand } from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import { ProgressStream } from '../../../components/ProgressStream';
import { ReportMarkdown } from '../../../components/ReportMarkdown';
import { StepsDrawer } from '../../../components/StepsDrawer';

type Params = Promise<{ id: string }>;

async function fetchReportMarkdown(s3Url: string): Promise<string> {
  try {
    const r = await fetch(s3Url, { cache: 'no-store' });
    if (!r.ok) return '';
    const json = await r.json();
    return (json?.report?.text as string) ?? '';
  } catch {
    return '';
  }
}

async function presignReportUrl(s3Key: string): Promise<string | null> {
  try {
    const s3 = new S3Client({
      region: process.env.AWS_REGION ?? 'us-east-1',
      endpoint: process.env.AWS_ENDPOINT_URL || undefined,
      credentials: {
        accessKeyId: process.env.AWS_ACCESS_KEY_ID ?? 'test',
        secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY ?? 'test',
      },
      forcePathStyle: true, // required for LocalStack
    });
    const cmd = new GetObjectCommand({
      Bucket: process.env.S3_BUCKET ?? 'agent-osint-local-results',
      Key: s3Key,
    });
    return await getSignedUrl(s3, cmd, { expiresIn: 3600 });
  } catch {
    return null;
  }
}

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

  // For done state, presign the S3 URL server-side and fetch the markdown report.
  let reportText = '';
  if (sc.status === 'completed' && sc.s3Key) {
    const presigned = await presignReportUrl(sc.s3Key);
    if (presigned) reportText = await fetchReportMarkdown(presigned);
  }

  return (
    <div>
      <div className="text-[10px] font-bold tracking-[0.1em] uppercase">
        SCAN · {shortId}
      </div>
      <h1 className="text-[18px] font-extrabold leading-[1.1]">
        {subject.toUpperCase()}
      </h1>
      {goal && <p className="text-[11px] text-muted mt-0.5">{goal}</p>}

      {(sc.status === 'queued' || sc.status === 'running') && (
        <>
          <ProgressStream
            scanId={id}
            initialStatus={sc.status}
            startedAt={sc.startedAt?.toISOString() ?? null}
          />
          <div className="mt-4.5 p-10 px-5 border-2 border-dashed border-dashed text-center min-h-[140px] flex flex-col justify-center items-center">
            <div className="label-uppercase text-muted2">REPORT</div>
            <div className="text-[13px] text-muted2 mt-1.5">
              Will appear when investigation completes…
            </div>
          </div>
        </>
      )}

      {sc.status === 'completed' && (
        <>
          <div className="mt-3.5 p-2 px-3.5 bg-white border-2 border-ink flex items-center gap-3.5 text-[10px] font-bold tracking-[0.08em]">
            <div>● COMPLETE</div>
            {sc.completedAt && sc.startedAt && (
              <div className="text-muted2">
                {Math.round(
                  (sc.completedAt.getTime() - sc.startedAt.getTime()) / 1000,
                )}s
              </div>
            )}
            {sc.totalCostUsd && (
              <div className="text-muted2">${Number(sc.totalCostUsd).toFixed(2)}</div>
            )}
            {sc.totalToolCalls !== null && (
              <div className="text-muted2">{sc.totalToolCalls} TOOL CALLS</div>
            )}
          </div>
          <div className="mt-4.5">
            {reportText
              ? <ReportMarkdown text={reportText} />
              : <p className="text-[13px] text-muted">Report unavailable — try refreshing.</p>}
          </div>
        </>
      )}

      {sc.status === 'failed' && (
        <>
          <div className="mt-3.5 p-2 px-3.5 bg-danger text-white flex items-center gap-3.5 text-[10px] font-bold tracking-[0.08em]">
            <div className="w-2 h-2 bg-amber rounded-full" />
            <div>FAILED</div>
            {sc.completedAt && sc.startedAt && (
              <div className="text-muted2">
                {Math.round(
                  (sc.completedAt.getTime() - sc.startedAt.getTime()) / 1000,
                )}s
              </div>
            )}
          </div>
          <div className="mt-3.5 p-3.5 px-4 bg-white border-2 border-danger">
            <div className="label-uppercase text-danger">ERROR</div>
            <div className="text-[13px] mt-1 text-[#1f1f1f] font-mono">
              {sc.errorMessage ?? 'Unknown error'}
            </div>
            <div className="text-[12px] text-muted mt-2">
              The investigation could not complete. The previous tool calls are
              preserved below.
            </div>
          </div>
        </>
      )}

      <StepsDrawer scanId={id} />
    </div>
  );
}
