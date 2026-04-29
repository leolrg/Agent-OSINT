import NextAuth from 'next-auth';
import Credentials from 'next-auth/providers/credentials';
import { compare } from 'bcrypt-ts';
import { eq } from 'drizzle-orm';
import { db } from './lib/db';
import { users } from './drizzle/schema';
import { authConfig } from './auth.config';

export const { handlers, signIn, signOut, auth } = NextAuth({
  ...authConfig,
  providers: [
    Credentials({
      credentials: {
        email: { label: 'Email', type: 'email' },
        password: { label: 'Password', type: 'password' },
      },
      async authorize(creds) {
        if (!creds?.email || !creds?.password) return null;
        const rows = await db.select().from(users)
          .where(eq(users.email, String(creds.email))).limit(1);
        const u = rows[0];
        if (!u) return null;
        const ok = await compare(String(creds.password), u.passwordHash);
        if (!ok) return null;
        return { id: u.id, email: u.email };
      },
    }),
  ],
});
