import type { NextAuthConfig } from 'next-auth';

// Edge-safe NextAuth config (no DB / Node-only imports). Used by
// middleware so it can run in the Edge runtime.
export const authConfig = {
  providers: [],
  session: { strategy: 'jwt' },
  trustHost: true,
  // NextAuth v5 prefers AUTH_SECRET; fall back to NEXTAUTH_SECRET so the
  // existing Phase 1 env var keeps working in middleware (Edge runtime).
  secret: process.env.AUTH_SECRET ?? process.env.NEXTAUTH_SECRET,
  pages: {
    signIn: '/auth/signin',
  },
} satisfies NextAuthConfig;
