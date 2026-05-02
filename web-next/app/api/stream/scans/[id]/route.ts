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
  const session = await auth();
  if (!session?.user?.id) {
    return new Response('unauthorized', { status: 401 });
  }

  const secretStr = process.env.AUTH_SECRET ?? process.env.NEXTAUTH_SECRET;
  if (!secretStr) {
    return new Response('server misconfigured', { status: 500 });
  }
  const secret = new TextEncoder().encode(secretStr);

  const token = await new SignJWT({
    sub: session.user.id,
    email: session.user.email ?? '',
  })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .setExpirationTime('1h')
    .sign(secret);

  const { id } = await params;
  const upstream = await fetch(`${API_BASE}/api/stream/scans/${id}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'text/event-stream',
    },
    cache: 'no-store',
  });

  if (!upstream.ok || !upstream.body) {
    return new Response(`upstream ${upstream.status}`, {
      status: upstream.status,
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
    },
  });
}
