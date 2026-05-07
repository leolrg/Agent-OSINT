import type { Config } from 'drizzle-kit';

export default {
  schema: './drizzle/schema.ts',
  out: './drizzle/migrations',
  dialect: 'postgresql',
  dbCredentials: {
    url: process.env.DATABASE_URL_NODE ?? 'postgresql://app:app@localhost:5432/agent_osint',
  },
  verbose: true,
  strict: true,
} satisfies Config;
