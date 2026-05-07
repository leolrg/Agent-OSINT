import { NextRequest } from 'next/server';
import { SignJWT } from 'jose';
import { auth } from '../../../../../auth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const API_BASE =
  process.env.API_BASE_INTERNAL ?? 'http://api-py:8000';

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (process.env.NODE_ENV !== 'production' && id === 'dev-mock') {
    return Response.json({
      steps: [
        {
          ts: 2,
          displayLabel: 'Web search',
          argSummary: '"Jane Doe ML"',
          fullArgs: { query: 'Jane Doe ML', max_results: 10 },
          responsePreview: '3 results\n2048 bytes',
        },
        {
          ts: 8,
          displayLabel: 'LinkedIn',
          argSummary: 'jane-doe-89a',
          fullArgs: { profile_url: 'https://www.linkedin.com/in/jane-doe-89a/' },
          responsePreview: '1 results\n4096 bytes',
        },
        {
          ts: 15,
          displayLabel: 'Maigret',
          argSummary: 'jdoe',
          fullArgs: { username: 'jdoe', sites_filter: ['GitHub', 'Reddit'] },
          responsePreview: 'Still running',
        },
      ],
    });
  }

  const session = await auth();
  if (!session?.user?.id) {
    return Response.json({ error: 'unauthorized' }, { status: 401 });
  }

  const secretStr = process.env.AUTH_SECRET ?? process.env.NEXTAUTH_SECRET;
  if (!secretStr) {
    return Response.json({ error: 'server misconfigured' }, { status: 500 });
  }

  const token = await new SignJWT({
    sub: session.user.id,
    email: session.user.email ?? '',
  })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .setExpirationTime('1h')
    .sign(new TextEncoder().encode(secretStr));

  const upstream = await fetch(`${API_BASE}/api/scans/${id}/steps`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/json',
    },
    cache: 'no-store',
  });

  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: {
      'Content-Type': upstream.headers.get('Content-Type') ?? 'application/json',
      'Cache-Control': 'no-store',
    },
  });
}
