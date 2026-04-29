'use server';

import { auth } from '../auth';
import { db } from '../lib/db';
import { scans } from '../drizzle/schema';
import { and, count, eq, inArray } from 'drizzle-orm';
import { enqueueScan } from '../lib/sqs';
import { redirect } from 'next/navigation';
import { fetchAgentCatalog } from '../lib/api';

export async function createScan(formData: FormData): Promise<void> {
  const session = await auth();
  if (!session?.user?.id) throw new Error('UNAUTHENTICATED');
  const userId = session.user.id;

  // Concurrency cap.
  const cap = Number(process.env.MAX_CONCURRENT_SCANS_PER_USER ?? '2');
  const inflight = await db.select({ n: count() }).from(scans).where(
    and(eq(scans.userId, userId), inArray(scans.status, ['queued', 'running'] as const)),
  );
  if ((inflight[0]?.n ?? 0) >= cap) {
    throw new Error(`You already have ${cap} scans in flight. Wait for one to finish.`);
  }

  // Validate against the manifest.
  const catalog = await fetchAgentCatalog();
  const agentName = String(formData.get('agent') ?? '');
  const agent = catalog.agents.find((a) => a.name === agentName);
  if (!agent) throw new Error('UNKNOWN_AGENT');

  const subject = String(formData.get('subject') ?? '').trim();
  if (!subject) throw new Error('SUBJECT_REQUIRED');

  const params: Record<string, unknown> = { subject, agent: agentName };
  for (const f of [...agent.params, ...catalog.common_params]) {
    const raw = formData.get(f.name);
    if (raw === null || raw === '') continue;
    switch (f.type) {
      case 'int': params[f.name] = parseInt(String(raw), 10); break;
      case 'float': params[f.name] = parseFloat(String(raw)); break;
      case 'bool': params[f.name] = raw === 'on' || raw === 'true'; break;
      default: params[f.name] = String(raw);
    }
  }

  // Insert scan row + enqueue.
  const [row] = await db.insert(scans).values({
    userId,
    status: 'queued',
    agent: agentName,
    params,
  }).returning({ id: scans.id });

  try {
    await enqueueScan(row.id, userId, params);
  } catch (err) {
    await db.update(scans).set({
      status: 'failed', errorMessage: 'enqueue_failed: ' + String(err).slice(0, 500),
    }).where(eq(scans.id, row.id));
    throw err;
  }

  redirect(`/scans/${row.id}`);
}
