'use server';

import { hash } from 'bcrypt-ts';
import { eq } from 'drizzle-orm';
import { redirect } from 'next/navigation';
import { db } from '../../../lib/db';
import { allowedEmails, users } from '../../../drizzle/schema';
import { signIn } from '../../../auth';

export async function createUser(formData: FormData): Promise<{ error?: string }> {
  const email = String(formData.get('email') ?? '').trim().toLowerCase();
  const password = String(formData.get('password') ?? '');
  if (!email || password.length < 12) {
    return { error: 'Email + password (≥12 chars) required.' };
  }

  // Invite gate.
  const allowed = await db.select().from(allowedEmails)
    .where(eq(allowedEmails.email, email)).limit(1);
  if (allowed.length === 0) {
    return { error: 'This email is not on the allowed list. Contact the admin.' };
  }

  // Reject if email already taken.
  const existing = await db.select().from(users).where(eq(users.email, email)).limit(1);
  if (existing.length > 0) {
    return { error: 'An account already exists for that email. Sign in instead.' };
  }

  const passwordHash = await hash(password, 12);
  await db.insert(users).values({ email, passwordHash });

  await signIn('credentials', { email, password, redirect: false });
  redirect('/scans');
}
