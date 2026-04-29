import { drizzle } from 'drizzle-orm/postgres-js';
import postgres from 'postgres';
import * as schema from '../drizzle/schema';

const url = process.env.DATABASE_URL_NODE
  ?? 'postgresql://app:app@localhost:5432/agent_osint';

export const sql = postgres(url, { max: 5 });
export const db = drizzle(sql, { schema });
