-- Initial schema for agent-osint.
-- Source of truth: this file. SQLAlchemy in osint/db/models.py mirrors it.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- for gen_random_uuid()

-- ----- Auth (NextAuth-compatible) -----

CREATE TABLE "users" (
  "id"              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "email"           text NOT NULL UNIQUE,
  "password_hash"   text NOT NULL,
  "email_verified"  timestamptz,
  "created_at"      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE "sessions" (
  "session_token"   text PRIMARY KEY,
  "user_id"         uuid NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "expires"         timestamptz NOT NULL
);

CREATE TABLE "allowed_emails" (
  "email"     text PRIMARY KEY,
  "added_at"  timestamptz NOT NULL DEFAULT now(),
  "added_by"  text
);

-- ----- Application -----

CREATE TABLE "scans" (
  "id"                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "user_id"           uuid NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "status"            text NOT NULL CHECK ("status" IN ('queued','running','completed','failed')),
  "agent"             text NOT NULL,
  "params"            jsonb NOT NULL,
  "s3_key"            text,
  "error_message"     text,
  "total_cost_usd"    numeric(10,4),
  "total_tool_calls"  integer,
  "created_at"        timestamptz NOT NULL DEFAULT now(),
  "started_at"        timestamptz,
  "completed_at"      timestamptz
);

CREATE INDEX "scans_user_created_idx"   ON "scans" ("user_id", "created_at" DESC);
CREATE INDEX "scans_status_started_idx" ON "scans" ("status", "started_at");

CREATE TABLE "scan_runs" (
  "id"           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  "scan_id"      uuid NOT NULL REFERENCES "scans"("id") ON DELETE CASCADE,
  "attempt"      integer NOT NULL,
  "worker_task"  text,
  "started_at"   timestamptz NOT NULL,
  "ended_at"     timestamptz,
  "outcome"      text
);
