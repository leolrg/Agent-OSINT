# Phase 1: Local Foundation + Worker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working local stack via Docker Compose where pushing an SQS message causes a Python worker to consume it, run an OSINT agent end-to-end, fan agent log events out to a Redis pub/sub channel, and write the final result to S3 (LocalStack) and Postgres. No UI in this phase — manual SQS messages are the test harness.

**Architecture:** Adds Postgres schema (Drizzle migrations are the source of truth; SQLAlchemy mirrors them in Python), a Redis-based `structlog` event sink that fans every agent log line into a `scan:{id}` Redis channel, and a Python worker container that calls `osint.run.scan(...)` directly inside its own process. Postgres + Redis run as real containers; S3 + SQS are faked by LocalStack. Drizzle migrations run in a one-shot `migrate` Compose service before the worker starts.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0 (sync), structlog (already in repo), `redis>=5`, `boto3`, `psycopg[binary]`, Docker Compose, LocalStack (S3+SQS), Drizzle ORM (Node 22) for migration files only, pytest.

**Phase context:** This is Phase 1 of 3 (per `2026-04-29-aws-deployment-design.md`). Phase 2 builds the FastAPI SSE service + Next.js UI on top of this. Phase 3 deploys to AWS via CDK. Phase 1 is gated done when `make smoke` runs an agent end-to-end through Docker Compose.

**Conventions for this plan:**
- Working dir is the repo root: `/Users/leolrg/Agent-OSINT/`.
- "Run X" steps assume bash/zsh.
- Each `git commit` step uses a Conventional Commits message; copy verbatim unless noted.
- Test files live under `tests/deploy/`. Existing agent tests under `tests/` are untouched.
- The phrase "the spec" refers to `docs/superpowers/specs/2026-04-29-aws-deployment-design.md`.

---

## File map

New files created across this plan:

```
.env.example                                           # documents every env var (Task 1)
.gitignore                                             # additions (Task 1)
Makefile                                               # dev shortcuts (Task 1, expanded in 12)
compose.yml                                            # local dev stack (Task 6)
infra/docker/worker/Dockerfile                         # worker image (Task 7)
infra/localstack/init/01_create_resources.sh          # bootstrap S3+SQS (Task 6)
osint/db/__init__.py                                   # SQLAlchemy package (Task 4)
osint/db/models.py                                     # SQLAlchemy ORM mirror (Task 4)
osint/db/session.py                                    # engine/session factory (Task 4)
osint/worker/__init__.py                               # worker package (Task 9)
osint/worker/config.py                                 # env config (Task 9)
osint/worker/event_sink.py                             # RedisEventSink (Task 8)
osint/worker/main.py                                   # SQS consumer loop (Task 9, expanded 10)
osint/worker/run_scan.py                               # ScanConfig builder + S3 upload (Task 10)
scripts/sweep_stuck_scans.py                          # operational sweeper (Task 11)
tests/deploy/__init__.py                              # (Task 1)
tests/deploy/test_redis_event_sink.py                 # (Task 8)
tests/deploy/test_schema_parity.py                    # (Task 5)
tests/deploy/test_stuck_scan_sweeper.py               # (Task 11)
tests/deploy/test_worker_loop.py                      # (Task 9)
tests/deploy/conftest.py                              # shared test fixtures (Task 5)
web-next/drizzle.config.ts                            # Drizzle config (Task 2)
web-next/drizzle/migrations/0000_initial.sql          # initial schema (Task 3)
web-next/drizzle/schema.ts                            # Drizzle schema declarations (Task 2)
web-next/package.json                                  # minimal package.json for Drizzle (Task 2)
web-next/tsconfig.json                                # minimal tsconfig (Task 2)
```

Modified files:

```
pyproject.toml                                         # new deps (Task 1)
```

---

## Task 1: Project structure, dependencies, environment file

**Files:**
- Modify: `pyproject.toml`
- Create: `.env.example`
- Modify: `.gitignore`
- Create: `Makefile`
- Create: `tests/deploy/__init__.py`
- Create: `osint/db/__init__.py`
- Create: `osint/worker/__init__.py`

- [ ] **Step 1.1: Add new Python dependencies**

Edit `pyproject.toml`. In the `dependencies` array, add (after `structlog`):

```toml
    "sqlalchemy>=2.0,<3.0",
    "psycopg[binary]>=3.1,<4.0",
    "redis>=5.0,<6.0",
    "boto3>=1.34,<2.0",
```

In `[project.optional-dependencies].dev`, add:

```toml
    "pytest-mock>=3.12",
    "moto[s3,sqs]>=5.0",  # in-process AWS mocks for unit tests
    "fakeredis[lua]>=2.21",
```

- [ ] **Step 1.2: Install the new deps**

Run: `pip install -e ".[dev]"`
Expected: All packages install without errors.

- [ ] **Step 1.3: Create empty package init files**

Run:
```bash
mkdir -p tests/deploy osint/db osint/worker scripts infra/docker/worker infra/localstack/init
touch tests/deploy/__init__.py osint/db/__init__.py osint/worker/__init__.py
```

- [ ] **Step 1.4: Create `.env.example`**

Write to `.env.example`:

```bash
# ============================================================
# Local development env file. Copy to `.env` and fill in.
# Production values come from AWS Secrets Manager (Phase 3).
# ============================================================

# --- LLM keys (real, used by the agent inside the worker) ---
OPENAI_API_KEY=sk-replace-me
XAI_API_KEY=xai-replace-me
APIFY_TOKEN=apify_api_replace_me

# --- Auth (Phase 2 only; harmless for Phase 1) ---
NEXTAUTH_SECRET=local-dev-secret-change-me-32-bytes-min

# --- Postgres ---
DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/agent_osint
# Drizzle uses postgres:// (no driver), so a separate var:
DATABASE_URL_NODE=postgresql://app:app@localhost:5432/agent_osint

# --- Redis ---
REDIS_URL=redis://localhost:6379/0

# --- LocalStack (S3 + SQS) ---
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
AWS_ENDPOINT_URL=http://localhost:4566
S3_BUCKET=agent-osint-local-results
SQS_QUEUE_URL=http://localhost:4566/000000000000/agent-osint-scans

# --- Worker tuning ---
LOG_LEVEL=INFO
SCAN_VISIBILITY_TIMEOUT_SECONDS=5400  # 90 min
SCAN_HEARTBEAT_SECONDS=300            # 5 min
```

- [ ] **Step 1.5: Update `.gitignore`**

Append to `.gitignore`:

```
# Phase 1 deployment additions
.env
.env.local
web-next/node_modules/
web-next/.next/
web-next/.drizzle-cache/
cdk.out/
infra/cdk/cdk.context.json
.localstack/
```

- [ ] **Step 1.6: Create initial `Makefile`**

Write to `Makefile` (use **TAB** indentation, not spaces):

```makefile
.PHONY: help dev down logs test smoke fmt

help:
	@echo "make dev       - bring up the local stack (postgres+redis+localstack+migrate+worker)"
	@echo "make down      - stop and remove all local-stack containers"
	@echo "make logs      - tail logs from all services"
	@echo "make test      - run pytest (unit + deploy tests)"
	@echo "make smoke     - end-to-end test: push an SQS message, observe completion"

dev:
	docker compose up -d

down:
	docker compose down -v

logs:
	docker compose logs -f --tail=100

test:
	pytest -v

smoke:
	@echo "Phase 1 smoke test target — implemented in Task 12"
```

- [ ] **Step 1.7: Verify everything still imports**

Run: `python -c "import osint, osint.db, osint.worker"`
Expected: No errors.

- [ ] **Step 1.8: Commit**

```bash
git add pyproject.toml .env.example .gitignore Makefile \
        tests/deploy/__init__.py osint/db/__init__.py osint/worker/__init__.py
git commit -m "feat(deploy): scaffold worker/db packages, env example, Makefile"
```

---

## Task 2: Initialize Drizzle for migration management

**Files:**
- Create: `web-next/package.json`
- Create: `web-next/tsconfig.json`
- Create: `web-next/drizzle.config.ts`
- Create: `web-next/drizzle/schema.ts`

The `web-next/` directory exists for Phase 2's Next.js app, but Phase 1 only uses it to host Drizzle. Drizzle migration files are the source of truth for the Postgres schema; SQLAlchemy mirrors them.

- [ ] **Step 2.1: Initialize `web-next/package.json`**

Write to `web-next/package.json`:

```json
{
  "name": "web-next",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "db:generate": "drizzle-kit generate",
    "db:migrate": "drizzle-kit migrate",
    "db:push": "drizzle-kit push"
  },
  "dependencies": {
    "drizzle-orm": "^0.30.0",
    "postgres": "^3.4.0"
  },
  "devDependencies": {
    "drizzle-kit": "^0.21.0",
    "typescript": "^5.4.0",
    "@types/node": "^20.11.0"
  }
}
```

- [ ] **Step 2.2: Add `web-next/tsconfig.json`**

Write to `web-next/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "noEmit": true
  },
  "include": ["drizzle/**/*.ts", "drizzle.config.ts"]
}
```

- [ ] **Step 2.3: Add `web-next/drizzle.config.ts`**

Write to `web-next/drizzle.config.ts`:

```ts
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
```

- [ ] **Step 2.4: Add `web-next/drizzle/schema.ts`**

This declares the Drizzle TypeScript schema. Tables match the spec's data layout section. Auth tables (`users`, `sessions`, `allowed_emails`) get fully wired up in Phase 2; here we only need the structure to exist for migrations.

Write to `web-next/drizzle/schema.ts`:

```ts
import {
  pgTable, uuid, text, timestamp, jsonb, integer, numeric, index,
} from 'drizzle-orm/pg-core';

export const users = pgTable('users', {
  id: uuid('id').primaryKey().defaultRandom(),
  email: text('email').notNull().unique(),
  passwordHash: text('password_hash').notNull(),
  emailVerified: timestamp('email_verified', { withTimezone: true }),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
});

export const sessions = pgTable('sessions', {
  sessionToken: text('session_token').primaryKey(),
  userId: uuid('user_id').notNull().references(() => users.id, { onDelete: 'cascade' }),
  expires: timestamp('expires', { withTimezone: true }).notNull(),
});

export const allowedEmails = pgTable('allowed_emails', {
  email: text('email').primaryKey(),
  addedAt: timestamp('added_at', { withTimezone: true }).notNull().defaultNow(),
  addedBy: text('added_by'),
});

export const scans = pgTable('scans', {
  id: uuid('id').primaryKey().defaultRandom(),
  userId: uuid('user_id').notNull().references(() => users.id, { onDelete: 'cascade' }),
  status: text('status').notNull(),  // CHECK constraint added in raw SQL migration
  agent: text('agent').notNull(),
  params: jsonb('params').notNull(),
  s3Key: text('s3_key'),
  errorMessage: text('error_message'),
  totalCostUsd: numeric('total_cost_usd', { precision: 10, scale: 4 }),
  totalToolCalls: integer('total_tool_calls'),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  startedAt: timestamp('started_at', { withTimezone: true }),
  completedAt: timestamp('completed_at', { withTimezone: true }),
}, (t) => ({
  userCreatedIdx: index('scans_user_created_idx').on(t.userId, t.createdAt.desc()),
  statusStartedIdx: index('scans_status_started_idx').on(t.status, t.startedAt),
}));

export const scanRuns = pgTable('scan_runs', {
  id: uuid('id').primaryKey().defaultRandom(),
  scanId: uuid('scan_id').notNull().references(() => scans.id, { onDelete: 'cascade' }),
  attempt: integer('attempt').notNull(),
  workerTask: text('worker_task'),
  startedAt: timestamp('started_at', { withTimezone: true }).notNull(),
  endedAt: timestamp('ended_at', { withTimezone: true }),
  outcome: text('outcome'),
});
```

- [ ] **Step 2.5: Verify drizzle-kit installs**

Run:
```bash
cd web-next && npm install --no-audit --no-fund
```
Expected: `node_modules/` populated; no fatal errors. (Warnings about peer deps are fine.)

- [ ] **Step 2.6: Commit**

```bash
git add web-next/package.json web-next/tsconfig.json web-next/drizzle.config.ts web-next/drizzle/schema.ts
git commit -m "feat(db): drizzle schema declarations for users/scans/allowed_emails"
```

---

## Task 3: Generate the initial Postgres migration

**Files:**
- Create: `web-next/drizzle/migrations/0000_initial.sql`
- Create: `web-next/drizzle/migrations/meta/_journal.json` (auto-generated)

We hand-write the migration rather than relying on `drizzle-kit generate` so the file is reviewable. Drizzle-kit's `migrate` command runs `.sql` files in the `out` dir in order; the `meta/_journal.json` tracks which have been applied.

- [ ] **Step 3.1: Write the SQL migration**

Write to `web-next/drizzle/migrations/0000_initial.sql`:

```sql
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
```

- [ ] **Step 3.2: Create the Drizzle journal file**

Write to `web-next/drizzle/migrations/meta/_journal.json`:

```json
{
  "version": "7",
  "dialect": "postgresql",
  "entries": [
    {
      "idx": 0,
      "version": "7",
      "when": 1714377600000,
      "tag": "0000_initial",
      "breakpoints": true
    }
  ]
}
```

The `when` field is a UNIX millisecond timestamp; the value above corresponds to 2024-04-29 (drizzle ignores it for ordering — `idx` is what matters).

- [ ] **Step 3.3: Smoke-test the migration against a throwaway Postgres**

Start a one-off Postgres locally to verify the SQL is valid:

```bash
docker run --rm -d --name pg-test -e POSTGRES_PASSWORD=app -e POSTGRES_USER=app -e POSTGRES_DB=agent_osint -p 5433:5432 postgres:16
sleep 3
psql postgresql://app:app@localhost:5433/agent_osint -f web-next/drizzle/migrations/0000_initial.sql
```

Expected output: `CREATE EXTENSION` then `CREATE TABLE` × 5, `CREATE INDEX` × 2, no errors.

Then:

```bash
psql postgresql://app:app@localhost:5433/agent_osint -c "\dt"
```

Expected: 5 tables — `allowed_emails`, `scan_runs`, `scans`, `sessions`, `users`.

Tear down: `docker rm -f pg-test`.

- [ ] **Step 3.4: Commit**

```bash
git add web-next/drizzle/migrations/
git commit -m "feat(db): initial schema migration (users, scans, scan_runs)"
```

---

## Task 4: SQLAlchemy schema mirror

**Files:**
- Create: `osint/db/models.py`
- Create: `osint/db/session.py`

Python uses SQLAlchemy 2.0 declarative against the same tables. This is read-and-write access for the worker (insert `scan_runs`, update `scans`); Phase 2's FastAPI uses it for read-only ownership checks.

- [ ] **Step 4.1: Write `osint/db/session.py`**

```python
"""SQLAlchemy engine + session factory.

Reads DATABASE_URL from the environment. Synchronous (psycopg) — the
worker is single-threaded per task, so async SQLAlchemy adds complexity
without benefit. Phase 2's FastAPI will add a separate async engine.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def _engine_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


_engine = create_engine(_engine_url(), pool_pre_ping=True, future=True)
SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=_engine, autoflush=False, expire_on_commit=False, future=True
)


@contextmanager
def db_session() -> Iterator[Session]:
    """Context manager — commits on clean exit, rolls back on exception."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
```

- [ ] **Step 4.2: Write `osint/db/models.py`**

```python
"""SQLAlchemy 2.0 declarative models mirroring the Drizzle schema in
web-next/drizzle/schema.ts. Drizzle migrations are the source of truth;
this file MUST stay in sync. tests/deploy/test_schema_parity.py enforces this.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          server_default=text("gen_random_uuid()"))
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    email_verified: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False,
                                                 server_default=text("now()"))


class Session(Base):
    __tablename__ = "sessions"
    session_token: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class AllowedEmail(Base):
    __tablename__ = "allowed_emails"
    email: Mapped[str] = mapped_column(Text, primary_key=True)
    added_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False,
                                               server_default=text("now()"))
    added_by: Mapped[Optional[str]] = mapped_column(Text)


class Scan(Base):
    __tablename__ = "scans"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','completed','failed')",
                        name="scans_status_check"),
        Index("scans_user_created_idx", "user_id", "created_at"),
        Index("scans_status_started_idx", "status", "started_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          server_default=text("gen_random_uuid()"))
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    s3_key: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    total_cost_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))
    total_tool_calls: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False,
                                                 server_default=text("now()"))
    started_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))


class ScanRun(Base):
    __tablename__ = "scan_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                          server_default=text("gen_random_uuid()"))
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_task: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    outcome: Mapped[Optional[str]] = mapped_column(Text)
```

- [ ] **Step 4.3: Verify imports cleanly**

Run: `python -c "from osint.db.models import User, Scan, ScanRun, Session, AllowedEmail; print('ok')"`
Expected: `ok`.

- [ ] **Step 4.4: Commit**

```bash
git add osint/db/session.py osint/db/models.py
git commit -m "feat(db): SQLAlchemy 2.0 models mirroring Drizzle schema"
```

---

## Task 5: Schema parity test

**Files:**
- Create: `tests/deploy/conftest.py`
- Create: `tests/deploy/test_schema_parity.py`

The test boots a fresh Postgres, applies the Drizzle SQL migration, then compares column names and types against the SQLAlchemy metadata. Catches drift.

- [ ] **Step 5.1: Add the shared fixture**

Write to `tests/deploy/conftest.py`:

```python
"""Shared fixtures for deployment tests.

`pg_url`: spins up a transient Postgres in Docker, applies the
Drizzle SQL migration, yields a SQLAlchemy URL, then tears down.
Tests using it must be marked `@pytest.mark.integration` so unit
runs (`pytest -m 'not integration'`) skip them.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_SQL = REPO_ROOT / "web-next" / "drizzle" / "migrations" / "0000_initial.sql"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def pg_url():
    port = _free_port()
    name = f"agent-osint-test-pg-{port}"
    subprocess.run([
        "docker", "run", "-d", "--rm", "--name", name,
        "-e", "POSTGRES_USER=app", "-e", "POSTGRES_PASSWORD=app",
        "-e", "POSTGRES_DB=agent_osint",
        "-p", f"{port}:5432",
        "postgres:16",
    ], check=True, capture_output=True)
    url = f"postgresql+psycopg://app:app@localhost:{port}/agent_osint"
    try:
        # Wait for readiness.
        engine = create_engine(url)
        for _ in range(30):
            try:
                with engine.connect() as c:
                    c.execute(text("SELECT 1"))
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("postgres did not become ready in 15s")
        # Apply migration.
        sql = MIGRATION_SQL.read_text()
        with engine.begin() as c:
            for stmt in [s for s in sql.split(";") if s.strip()]:
                c.execute(text(stmt))
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
```

- [ ] **Step 5.2: Write the failing test**

Write to `tests/deploy/test_schema_parity.py`:

```python
"""Verify SQLAlchemy column metadata matches the Drizzle migration output.

The Drizzle SQL is the source of truth. SQLAlchemy in osint/db/models.py
must declare columns with matching names and base types. Catches drift
when one side gets edited but not the other.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect

from osint.db.models import Base


pytestmark = pytest.mark.integration


def test_sqlalchemy_metadata_matches_postgres(pg_url):
    engine = create_engine(pg_url)
    inspector = inspect(engine)
    pg_tables = {t for t in inspector.get_table_names() if not t.startswith("_")}

    sa_tables = set(Base.metadata.tables.keys())
    assert sa_tables == pg_tables, (
        f"SQLAlchemy and Postgres tables differ\n"
        f"  Only in SQLAlchemy: {sa_tables - pg_tables}\n"
        f"  Only in Postgres:   {pg_tables - sa_tables}"
    )

    for table in sa_tables:
        sa_cols = {c.name for c in Base.metadata.tables[table].columns}
        pg_cols = {c["name"] for c in inspector.get_columns(table)}
        assert sa_cols == pg_cols, (
            f"Column drift in `{table}`\n"
            f"  Only in SQLAlchemy: {sa_cols - pg_cols}\n"
            f"  Only in Postgres:   {pg_cols - sa_cols}"
        )
```

- [ ] **Step 5.3: Register the marker so pytest doesn't warn**

In `pyproject.toml`, in `[tool.pytest.ini_options]`, change/add:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "integration: spins up real services (docker) — slower",
]
```

- [ ] **Step 5.4: Run the test**

Run: `pytest tests/deploy/test_schema_parity.py -v`
Expected: PASS. (Takes ~10s — pulls/starts Postgres.)

If it fails because tables don't match, fix the side that's wrong. Drizzle SQL is the source of truth.

- [ ] **Step 5.5: Commit**

```bash
git add tests/deploy/conftest.py tests/deploy/test_schema_parity.py pyproject.toml
git commit -m "test(deploy): schema parity between Drizzle SQL and SQLAlchemy"
```

---

## Task 6: Docker Compose — local stack

**Files:**
- Create: `compose.yml`
- Create: `infra/localstack/init/01_create_resources.sh`

Brings up Postgres, Redis, LocalStack (S3+SQS), and a one-shot `migrate` service that runs Drizzle migrations. Worker container is added in Task 9.

- [ ] **Step 6.1: Write the LocalStack init script**

LocalStack runs every executable in `/etc/localstack/init/ready.d/` once it's ready. We use it to create the bucket and queue.

Write to `infra/localstack/init/01_create_resources.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

awslocal s3 mb s3://agent-osint-local-results
awslocal sqs create-queue \
  --queue-name agent-osint-scans \
  --attributes '{"VisibilityTimeout":"5400"}'  # 90 min
echo "[init] localstack bucket and queue created"
```

Make it executable: `chmod +x infra/localstack/init/01_create_resources.sh`

- [ ] **Step 6.2: Write `compose.yml`**

Write to `compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: agent_osint
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d agent_osint"]
      interval: 2s
      timeout: 2s
      retries: 20
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 2s
      retries: 20

  localstack:
    image: localstack/localstack:3
    environment:
      SERVICES: s3,sqs
      DEFAULT_REGION: us-east-1
      AWS_ACCESS_KEY_ID: test
      AWS_SECRET_ACCESS_KEY: test
    ports: ["4566:4566"]
    volumes:
      - ./infra/localstack/init:/etc/localstack/init/ready.d
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:4566/_localstack/health"]
      interval: 3s
      timeout: 3s
      retries: 30

  migrate:
    image: node:22-alpine
    working_dir: /app/web-next
    environment:
      DATABASE_URL_NODE: postgresql://app:app@postgres:5432/agent_osint
    volumes:
      - ./web-next:/app/web-next
    command: ["sh", "-c", "npm install --no-audit --no-fund && npx drizzle-kit migrate"]
    depends_on:
      postgres:
        condition: service_healthy

  worker:
    build:
      context: .
      dockerfile: infra/docker/worker/Dockerfile
    environment:
      DATABASE_URL: postgresql+psycopg://app:app@postgres:5432/agent_osint
      REDIS_URL: redis://redis:6379/0
      AWS_REGION: us-east-1
      AWS_ACCESS_KEY_ID: test
      AWS_SECRET_ACCESS_KEY: test
      AWS_ENDPOINT_URL: http://localstack:4566
      S3_BUCKET: agent-osint-local-results
      SQS_QUEUE_URL: http://localstack:4566/000000000000/agent-osint-scans
      SCAN_VISIBILITY_TIMEOUT_SECONDS: "5400"
      SCAN_HEARTBEAT_SECONDS: "300"
      LOG_LEVEL: INFO
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
      XAI_API_KEY: ${XAI_API_KEY:-}
      APIFY_TOKEN: ${APIFY_TOKEN:-}
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
      localstack: { condition: service_healthy }
      migrate:  { condition: service_completed_successfully }

volumes:
  pgdata:
```

- [ ] **Step 6.3: Verify compose validates**

Run: `docker compose config --quiet`
Expected: No output, exit 0.

- [ ] **Step 6.4: Commit**

```bash
git add compose.yml infra/localstack/init/
git commit -m "feat(deploy): docker compose stack — postgres+redis+localstack+migrate+worker"
```

---

## Task 7: Worker Dockerfile

**Files:**
- Create: `infra/docker/worker/Dockerfile`

- [ ] **Step 7.1: Write the Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps for psycopg + lxml/maigret/etc. (lean: only what fails install otherwise)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching.
COPY pyproject.toml /app/
COPY osint/__init__.py /app/osint/__init__.py
RUN pip install -e ".[dev]" || true  # tolerate dev deps; refined below

# Now copy the rest of the package.
COPY osint /app/osint
COPY scripts /app/scripts

# Re-install once full source is present so editable install indexes everything.
RUN pip install -e "."

# Worker entrypoint.
CMD ["python", "-m", "osint.worker.main"]
```

- [ ] **Step 7.2: Build it**

Run: `docker compose build worker`
Expected: Image builds, ends with `Successfully tagged ...`. Takes 1–3 min the first time.

- [ ] **Step 7.3: Commit**

```bash
git add infra/docker/worker/Dockerfile
git commit -m "feat(deploy): worker Dockerfile (python:3.11-slim)"
```

---

## Task 8: RedisEventSink (structlog processor)

**Files:**
- Create: `osint/worker/event_sink.py`
- Create: `tests/deploy/test_redis_event_sink.py`

The event sink is a structlog processor — a callable that gets every log event before it hits the renderer. It publishes the event to a Redis channel keyed by `scan_id`, plus pushes onto a capped history list for late SSE subscribers.

- [ ] **Step 8.1: Write the failing test**

Write to `tests/deploy/test_redis_event_sink.py`:

```python
"""Unit tests for RedisEventSink — using fakeredis, no real Redis."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import fakeredis
import pytest

from osint.worker.event_sink import RedisEventSink


def test_publishes_to_scan_channel():
    redis = fakeredis.FakeRedis()
    pubsub = redis.pubsub()
    pubsub.subscribe("scan:abc")
    next(pubsub.listen())  # consume subscribe-ack

    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    sink(logger=None, method_name="info",
         event_dict={"event": "tool.started", "tool": "google_search"})

    msg = next(pubsub.listen())
    assert msg["type"] == "message"
    payload = json.loads(msg["data"])
    assert payload["event"] == "tool.started"
    assert payload["tool"] == "google_search"
    assert payload["level"] == "info"
    assert "ts" in payload


def test_appends_to_history_list_with_cap():
    redis = fakeredis.FakeRedis()
    sink = RedisEventSink(scan_id="abc", redis_client=redis, history_cap=5)
    for i in range(10):
        sink(logger=None, method_name="info", event_dict={"event": f"e{i}"})
    history = redis.lrange("scan:abc:events", 0, -1)
    assert len(history) == 5
    # LPUSH stores newest-first; last 5 events should be e5..e9.
    events = [json.loads(h) for h in history]
    assert [e["event"] for e in events] == ["e9", "e8", "e7", "e6", "e5"]


def test_swallows_redis_errors():
    redis = MagicMock()
    redis.publish.side_effect = ConnectionError("boom")
    redis.lpush.side_effect = ConnectionError("boom")
    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    # Should not raise — agent must not break when Redis is down.
    out = sink(logger=None, method_name="info", event_dict={"event": "x"})
    assert out == {"event": "x"}  # passes through to next processor


def test_passes_through_event_dict_unchanged():
    redis = fakeredis.FakeRedis()
    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    in_dict = {"event": "x", "n": 1}
    out = sink(logger=None, method_name="info", event_dict=in_dict)
    assert out is in_dict  # structlog requires the same dict back
```

- [ ] **Step 8.2: Run to verify it fails**

Run: `pytest tests/deploy/test_redis_event_sink.py -v`
Expected: FAIL — `ImportError: cannot import name 'RedisEventSink'`.

- [ ] **Step 8.3: Implement `RedisEventSink`**

Write to `osint/worker/event_sink.py`:

```python
"""structlog processor that fans agent log events out to Redis.

Used in the worker only. CLI runs (osint.cli) leave the structlog
config alone — see osint/log.py for the default chain.

Design:
- Best-effort. Any Redis error is swallowed so the agent keeps running.
- Maintains a capped history list (default 100 events, 24h TTL) so
  SSE clients that connect mid-scan can replay recent events.
- Returns the event_dict unchanged so other processors (e.g. the
  console renderer) still get to write to stdout.
"""
from __future__ import annotations

import json
import time
from typing import Any

import redis as _redis


class RedisEventSink:
    HISTORY_CAP_DEFAULT = 100
    HISTORY_TTL_SECONDS = 86400  # 24h

    def __init__(
        self,
        scan_id: str,
        redis_client: _redis.Redis,
        history_cap: int = HISTORY_CAP_DEFAULT,
    ) -> None:
        self.scan_id = scan_id
        self.redis = redis_client
        self.history_cap = history_cap
        self.channel = f"scan:{scan_id}"
        self.history_key = f"scan:{scan_id}:events"

    def __call__(self, logger: Any, method_name: str, event_dict: dict) -> dict:
        payload = json.dumps({
            "ts": time.time(),
            "level": method_name,
            **event_dict,
        }, default=str)
        try:
            self.redis.publish(self.channel, payload)
            self.redis.lpush(self.history_key, payload)
            self.redis.ltrim(self.history_key, 0, self.history_cap - 1)
            self.redis.expire(self.history_key, self.HISTORY_TTL_SECONDS)
        except Exception:
            # Never let event sink failure break the agent.
            pass
        return event_dict
```

- [ ] **Step 8.4: Run tests to verify they pass**

Run: `pytest tests/deploy/test_redis_event_sink.py -v`
Expected: 4 tests PASS.

- [ ] **Step 8.5: Commit**

```bash
git add osint/worker/event_sink.py tests/deploy/test_redis_event_sink.py
git commit -m "feat(worker): RedisEventSink structlog processor + tests"
```

---

## Task 9: Worker config + SQS consumer skeleton

**Files:**
- Create: `osint/worker/config.py`
- Create: `osint/worker/main.py` (skeleton — Task 10 wires in scan execution)
- Create: `tests/deploy/test_worker_loop.py`

The skeleton consumes one SQS message, claims it (DB row → `running`, insert `scan_runs`), publishes a `scan.started` event, then immediately publishes `scan.completed` and acks. Task 10 replaces the stubbed body with the real `run_scan(...)` call.

- [ ] **Step 9.1: Write `osint/worker/config.py`**

```python
"""Worker config from env. All Phase 3 production values come from
AWS Secrets Manager + ECS env vars; the names match here so Phase 3
just changes the source, not the code."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerConfig:
    database_url: str
    redis_url: str
    aws_region: str
    aws_endpoint_url: str | None
    s3_bucket: str
    sqs_queue_url: str
    visibility_timeout_seconds: int
    heartbeat_seconds: int
    log_level: str

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        return cls(
            database_url=os.environ["DATABASE_URL"],
            redis_url=os.environ["REDIS_URL"],
            aws_region=os.environ["AWS_REGION"],
            aws_endpoint_url=os.environ.get("AWS_ENDPOINT_URL") or None,
            s3_bucket=os.environ["S3_BUCKET"],
            sqs_queue_url=os.environ["SQS_QUEUE_URL"],
            visibility_timeout_seconds=int(os.environ.get("SCAN_VISIBILITY_TIMEOUT_SECONDS", "5400")),
            heartbeat_seconds=int(os.environ.get("SCAN_HEARTBEAT_SECONDS", "300")),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
```

- [ ] **Step 9.2: Write the failing test**

Write to `tests/deploy/test_worker_loop.py`:

```python
"""Unit tests for the worker SQS-consume loop using moto + fakeredis.

Phase 1 stub: the worker consumes a message, claims the scan in DB,
publishes scan.started + scan.completed, acks. Real run_scan() call
arrives in Task 10.
"""
from __future__ import annotations

import json
import os
import uuid
from unittest.mock import patch

import boto3
import fakeredis
import pytest
from moto import mock_aws

from osint.db.models import Base, Scan, User
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.integration


@pytest.fixture
def db(pg_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    engine = create_engine(pg_url)
    Sess = sessionmaker(bind=engine)
    yield Sess


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis()
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    monkeypatch.setattr("osint.worker.main._redis_client", lambda url: fake)
    return fake


def _seed_user_and_scan(Sess) -> tuple[uuid.UUID, uuid.UUID]:
    with Sess() as s:
        u = User(email="a@example.com", password_hash="x")
        s.add(u); s.flush()
        sc = Scan(user_id=u.id, status="queued", agent="critic_react_v3",
                  params={"subject": "Jane Doe"})
        s.add(sc); s.flush()
        s.commit()
        return u.id, sc.id


@mock_aws
def test_consume_one_message_marks_running_then_completed(db, aws_env, fake_redis,
                                                          monkeypatch):
    sqs = boto3.client("sqs", region_name="us-east-1")
    queue_url = sqs.create_queue(QueueName="t")["QueueUrl"]
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="agent-osint-test")

    monkeypatch.setenv("SQS_QUEUE_URL", queue_url)
    monkeypatch.setenv("S3_BUCKET", "agent-osint-test")
    monkeypatch.setenv("SCAN_VISIBILITY_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("SCAN_HEARTBEAT_SECONDS", "30")

    user_id, scan_id = _seed_user_and_scan(db)
    sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps({
        "scan_id": str(scan_id), "user_id": str(user_id),
        "params": {"subject": "Jane Doe", "agent": "critic_react_v3"},
    }))

    # Stub run_scan so Task 9's test doesn't need a real LLM.
    with patch("osint.worker.run_scan.execute_scan", return_value={
        "ok": True,
        "result_bytes": b'{"scan": "stub"}',
        "total_cost_usd": 0.0,
        "total_tool_calls": 0,
    }):
        from osint.worker.main import run_once
        run_once()

    with db() as s:
        sc = s.get(Scan, scan_id)
        assert sc.status == "completed"
        assert sc.s3_key is not None
        assert sc.completed_at is not None
```

- [ ] **Step 9.3: Run to verify it fails**

Run: `pytest tests/deploy/test_worker_loop.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_once'`.

- [ ] **Step 9.4: Implement worker skeleton**

Write to `osint/worker/main.py`:

```python
"""Worker entrypoint.

Long-poll SQS for one message, run the scan, ack on success. ECS
runs `python -m osint.worker.main` which loops forever; tests call
run_once() directly.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone

import boto3
import redis
import structlog

from osint.db.models import Scan, ScanRun
from osint.db.session import db_session
from osint.log import configure_logging
from osint.worker.config import WorkerConfig
from osint.worker.event_sink import RedisEventSink
from osint.worker.run_scan import execute_scan


def _redis_client(url: str) -> redis.Redis:
    return redis.from_url(url)


def _sqs_client(cfg: WorkerConfig):
    return boto3.client(
        "sqs", region_name=cfg.aws_region,
        endpoint_url=cfg.aws_endpoint_url,
    )


def _s3_client(cfg: WorkerConfig):
    return boto3.client(
        "s3", region_name=cfg.aws_region,
        endpoint_url=cfg.aws_endpoint_url,
    )


def _configure_logging_with_event_sink(scan_id: str, redis_client) -> RedisEventSink:
    """Add RedisEventSink to the structlog processor chain in front of the renderer.

    osint/log.py installs ConsoleRenderer as the last processor; we splice
    our sink before it so we still get stdout logs *plus* Redis fanout.
    """
    configure_logging()
    cfg = structlog.get_config()
    procs = list(cfg["processors"])
    sink = RedisEventSink(scan_id=scan_id, redis_client=redis_client)
    # Insert before the renderer (last entry).
    procs.insert(len(procs) - 1, sink)
    structlog.configure(processors=procs,
                        wrapper_class=cfg["wrapper_class"],
                        logger_factory=cfg["logger_factory"])
    return sink


def _publish_terminal(redis_client, scan_id: str, event: str, **fields) -> None:
    payload = json.dumps({"ts": time.time(), "level": "info",
                          "event": event, "scan_id": scan_id, **fields})
    try:
        redis_client.publish(f"scan:{scan_id}", payload)
        redis_client.lpush(f"scan:{scan_id}:events", payload)
        redis_client.ltrim(f"scan:{scan_id}:events", 0, 99)
        redis_client.expire(f"scan:{scan_id}:events", 86400)
    except Exception:
        pass


def run_once() -> bool:
    """Process one SQS message. Returns True if a message was handled, False if none."""
    cfg = WorkerConfig.from_env()
    sqs = _sqs_client(cfg)
    s3 = _s3_client(cfg)
    rds = _redis_client(cfg.redis_url)

    resp = sqs.receive_message(
        QueueUrl=cfg.sqs_queue_url,
        WaitTimeSeconds=20,
        MaxNumberOfMessages=1,
        VisibilityTimeout=cfg.visibility_timeout_seconds,
    )
    msgs = resp.get("Messages") or []
    if not msgs:
        return False

    msg = msgs[0]
    body = json.loads(msg["Body"])
    scan_id = uuid.UUID(body["scan_id"])
    user_id = uuid.UUID(body["user_id"])
    params = body["params"]

    _configure_logging_with_event_sink(str(scan_id), rds)
    log = structlog.get_logger("worker").bind(scan_id=str(scan_id))

    # Claim: status=running + insert scan_runs row.
    with db_session() as s:
        sc = s.get(Scan, scan_id)
        if sc is None:
            log.error("scan.row_missing")
            sqs.delete_message(QueueUrl=cfg.sqs_queue_url, ReceiptHandle=msg["ReceiptHandle"])
            return True
        sc.status = "running"
        sc.started_at = datetime.now(timezone.utc)
        prev_attempts = s.query(ScanRun).filter_by(scan_id=scan_id).count()
        s.add(ScanRun(scan_id=scan_id, attempt=prev_attempts + 1,
                      worker_task=os.environ.get("HOSTNAME"),
                      started_at=datetime.now(timezone.utc)))
    log.info("scan.started", agent=params.get("agent"))

    try:
        outcome = execute_scan(scan_id=str(scan_id), params=params)
    except Exception as e:  # noqa: BLE001
        log.exception("scan.failed", error=str(e))
        with db_session() as s:
            sc = s.get(Scan, scan_id)
            sc.status = "failed"
            sc.error_message = str(e)[:1000]
            sc.completed_at = datetime.now(timezone.utc)
        _publish_terminal(rds, str(scan_id), "scan.failed", error=str(e)[:200])
        sqs.delete_message(QueueUrl=cfg.sqs_queue_url, ReceiptHandle=msg["ReceiptHandle"])
        return True

    # Upload result to S3.
    s3_key = f"scans/{user_id}/{scan_id}.json"
    s3.put_object(Bucket=cfg.s3_bucket, Key=s3_key, Body=outcome["result_bytes"],
                  ContentType="application/json")

    with db_session() as s:
        sc = s.get(Scan, scan_id)
        sc.status = "completed"
        sc.s3_key = s3_key
        sc.total_cost_usd = outcome.get("total_cost_usd")
        sc.total_tool_calls = outcome.get("total_tool_calls")
        sc.completed_at = datetime.now(timezone.utc)

    _publish_terminal(rds, str(scan_id), "scan.completed", s3_key=s3_key)
    sqs.delete_message(QueueUrl=cfg.sqs_queue_url, ReceiptHandle=msg["ReceiptHandle"])
    log.info("scan.completed", s3_key=s3_key)
    return True


def main() -> int:
    cfg = WorkerConfig.from_env()
    logging.basicConfig(level=getattr(logging, cfg.log_level))
    stop = False

    def _on_signal(*_):
        nonlocal stop; stop = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    while not stop:
        try:
            run_once()
        except Exception:
            structlog.get_logger("worker").exception("worker.loop_error")
            time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 9.5: Implement a stub `execute_scan` so this task's test can pass**

Write to `osint/worker/run_scan.py`:

```python
"""Wrapper that turns SQS params into a ScanConfig and runs the agent.

Phase 1 Task 9: stub returns canned bytes (test patches it).
Phase 1 Task 10: real implementation calling osint.run.scan(...).
"""
from __future__ import annotations

from typing import Any


def execute_scan(*, scan_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run the agent. Returns dict with `result_bytes`, `total_cost_usd`,
    `total_tool_calls`. Raises on failure.
    """
    raise NotImplementedError("Real implementation lands in Task 10")
```

- [ ] **Step 9.6: Run worker test**

Run: `pytest tests/deploy/test_worker_loop.py -v`
Expected: PASS — the test patches `execute_scan` so the stub doesn't actually run.

- [ ] **Step 9.7: Commit**

```bash
git add osint/worker/config.py osint/worker/main.py osint/worker/run_scan.py \
        tests/deploy/test_worker_loop.py
git commit -m "feat(worker): SQS consumer skeleton + claim/ack flow + tests"
```

---

## Task 10: Real scan execution

**Files:**
- Modify: `osint/worker/run_scan.py`

Replaces the stub with a real call to `osint.run.scan(...)`. Reads the JSON file the existing pipeline writes to a tempdir, returns its bytes.

- [ ] **Step 10.1: Implement `execute_scan`**

Replace the contents of `osint/worker/run_scan.py`:

```python
"""Wrapper that turns SQS params into a ScanConfig and runs the agent.

The agent (osint.run.scan) writes a JSON file to scans_dir; we use a
tempdir, read the resulting file, and return its bytes for the worker
to upload to S3.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from osint.run import scan as run_scan_async
from osint.types import ScanConfig


def _build_config(params: dict[str, Any]) -> ScanConfig:
    """Translate SQS params into a ScanConfig.

    SQS params shape (matches Phase 2 Next.js submit form):
      { subject, agent, preset?, goal?, budget_usd?, max_calls?, max_seconds?, ... }
    Anything not provided falls back to ScanConfig defaults.
    """
    kwargs: dict[str, Any] = {}
    if "agent" in params:
        kwargs["agent_version"] = params["agent"]
    if "budget_usd" in params:
        kwargs["budget_usd"] = float(params["budget_usd"])
    if "max_calls" in params:
        kwargs["max_calls"] = int(params["max_calls"])
    if "max_seconds" in params:
        kwargs["max_seconds"] = int(params["max_seconds"])
    if "preset" in params:
        kwargs["preset"] = params["preset"]
    if "goal" in params:
        kwargs["goal"] = params["goal"]
    return ScanConfig(**kwargs)


def execute_scan(*, scan_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run the agent end-to-end.

    Returns: { result_bytes: bytes, total_cost_usd: float, total_tool_calls: int }
    Raises whatever the agent raises (worker catches and marks failed).
    """
    subject = params.get("subject")
    if not subject:
        raise ValueError("params.subject is required")
    config = _build_config(params)

    with tempfile.TemporaryDirectory() as tmp:
        scans_dir = Path(tmp)
        result = asyncio.run(run_scan_async(
            subject=subject, config=config, scans_dir=scans_dir,
        ))
        # The agent writes <internal_scan_id>.json into scans_dir. Find it.
        json_files = list(scans_dir.glob("*.json"))
        if not json_files:
            raise RuntimeError("agent produced no scan JSON")
        result_bytes = json_files[0].read_bytes()

    # ScanResult shape — defensive; field names may evolve.
    total_cost_usd = getattr(result, "total_cost_usd", None) \
        or getattr(getattr(result, "state", None), "total_cost_usd", None) \
        or 0.0
    total_tool_calls = getattr(result, "total_tool_calls", None) \
        or getattr(getattr(result, "state", None), "total_tool_calls", None) \
        or 0

    return {
        "result_bytes": result_bytes,
        "total_cost_usd": float(total_cost_usd),
        "total_tool_calls": int(total_tool_calls),
    }
```

> **Note on `ScanConfig` kwargs:** the spec's brainstorming captured that the agent set is fluid — fields like `preset` and `goal` are critic_react_v3-specific and may not exist on `ScanConfig`. If a `**kwargs` field rejects unknown keys, this function will raise `TypeError`. **Verify by reading `osint/types.py` before this step:** if `ScanConfig` does NOT accept `preset`/`goal` directly, drop those lines and store them under `params` for agent-specific lookup, OR add them to `ScanConfig` — whichever matches the existing pattern in `osint/cli.py`.

- [ ] **Step 10.2: Verify ScanConfig accepts the kwargs**

Run: `python -c "from osint.types import ScanConfig; sc = ScanConfig(); print([f for f in sc.model_fields])"`

If `preset`, `goal`, `agent_version` are listed: continue.
If they're missing: open `osint/cli.py` and look at how it builds `ScanConfig`. Match that pattern in `_build_config` above.

- [ ] **Step 10.3: Run unit tests**

Run: `pytest tests/deploy/test_worker_loop.py -v`
Expected: PASS (it patches `execute_scan` so this code path isn't exercised yet — Task 12 covers the real run end-to-end via Compose).

- [ ] **Step 10.4: Commit**

```bash
git add osint/worker/run_scan.py
git commit -m "feat(worker): real scan execution via osint.run.scan + S3 upload"
```

---

## Task 11: Stuck-scan sweeper

**Files:**
- Create: `scripts/sweep_stuck_scans.py`
- Create: `tests/deploy/test_stuck_scan_sweeper.py`

A standalone Python script. Phase 3 wires it to a CloudWatch Events schedule via Lambda or ECS scheduled task; locally it's just `python scripts/sweep_stuck_scans.py`.

- [ ] **Step 11.1: Write the failing test**

Write to `tests/deploy/test_stuck_scan_sweeper.py`:

```python
"""Sweeper marks scans stuck in `running` for >threshold as `failed`."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from osint.db.models import Scan, User


pytestmark = pytest.mark.integration


def test_sweeper_marks_only_stuck_scans(pg_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    engine = create_engine(pg_url)
    Sess = sessionmaker(bind=engine)

    with Sess() as s:
        u = User(email="a@example.com", password_hash="x")
        s.add(u); s.flush()
        now = datetime.now(timezone.utc)
        # Stuck (>90 min in running)
        s.add(Scan(user_id=u.id, status="running",
                   started_at=now - timedelta(minutes=120),
                   agent="x", params={}))
        # Fresh (still legitimately running)
        s.add(Scan(user_id=u.id, status="running",
                   started_at=now - timedelta(minutes=5),
                   agent="x", params={}))
        # Already-completed (must NOT be touched)
        s.add(Scan(user_id=u.id, status="completed",
                   started_at=now - timedelta(minutes=200),
                   completed_at=now - timedelta(minutes=10),
                   agent="x", params={}))
        s.commit()

    from scripts.sweep_stuck_scans import sweep
    n = sweep(threshold_minutes=90)
    assert n == 1

    with Sess() as s:
        rows = s.query(Scan).order_by(Scan.started_at).all()
        assert rows[0].status == "failed"
        assert rows[0].error_message == "worker_timeout"
        assert rows[1].status == "completed"  # untouched
        assert rows[2].status == "running"    # fresh, untouched
```

- [ ] **Step 11.2: Run to verify failure**

Run: `pytest tests/deploy/test_stuck_scan_sweeper.py -v`
Expected: FAIL with `ImportError: cannot import name 'sweep'`.

- [ ] **Step 11.3: Implement the sweeper**

Write to `scripts/sweep_stuck_scans.py`:

```python
"""Mark scans stuck in `running` longer than the threshold as failed.

Designed to run on a 10-minute cron in production. Idempotent —
running twice in a row is a no-op the second time.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from osint.db.models import Scan
from osint.db.session import db_session


def sweep(*, threshold_minutes: int = 90, redis_url: str | None = None) -> int:
    """Returns count of scans marked failed."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
    n = 0
    with db_session() as s:
        rows = s.execute(
            select(Scan).where(Scan.status == "running", Scan.started_at < cutoff)
        ).scalars().all()
        for sc in rows:
            sc.status = "failed"
            sc.error_message = "worker_timeout"
            sc.completed_at = datetime.now(timezone.utc)
            n += 1

    if n and (redis_url or os.environ.get("REDIS_URL")):
        # Best-effort terminal event so any open SSE clients close.
        import redis as _redis  # noqa: WPS433
        try:
            r = _redis.from_url(redis_url or os.environ["REDIS_URL"])
            for sc in rows:
                payload = json.dumps({"ts": time.time(), "level": "info",
                                      "event": "scan.failed",
                                      "scan_id": str(sc.id),
                                      "error": "worker_timeout"})
                r.publish(f"scan:{sc.id}", payload)
        except Exception:
            pass

    return n


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--threshold-minutes", type=int, default=90)
    args = p.parse_args()
    n = sweep(threshold_minutes=args.threshold_minutes)
    print(f"swept {n} stuck scans")
    sys.exit(0)
```

- [ ] **Step 11.4: Run the test**

Run: `pytest tests/deploy/test_stuck_scan_sweeper.py -v`
Expected: PASS.

- [ ] **Step 11.5: Commit**

```bash
git add scripts/sweep_stuck_scans.py tests/deploy/test_stuck_scan_sweeper.py
git commit -m "feat(ops): stuck-scan sweeper script + test"
```

---

## Task 12: End-to-end smoke test via Docker Compose

**Files:**
- Modify: `Makefile`
- Create: `scripts/smoke_test.sh`

The smoke test brings up the full stack, seeds a user + scan row in Postgres, drops an SQS message, and verifies the scan reaches `completed` status with an S3 object created.

- [ ] **Step 12.1: Write the smoke script**

Write to `scripts/smoke_test.sh`:

```bash
#!/usr/bin/env bash
# End-to-end smoke test for Phase 1.
# 1. Bring up the compose stack.
# 2. Seed a user + scan row in Postgres.
# 3. Drop an SQS message referencing that scan.
# 4. Poll Postgres for status='completed' (or 'failed') with timeout.
# 5. Tear down on success.
set -euo pipefail

if [ ! -f .env ]; then
    echo "ERROR: .env missing. Copy .env.example and fill in API keys." >&2
    exit 1
fi
# shellcheck disable=SC1091
source .env

echo "[1/5] Bringing up the stack..."
docker compose up -d --wait

echo "[2/5] Seeding user + scan row..."
SUBJECT="${SMOKE_SUBJECT:-Jane Doe smoke test}"
read -r USER_ID SCAN_ID < <(docker compose exec -T postgres psql -U app -d agent_osint -tA -F' ' <<SQL
INSERT INTO users (email, password_hash)
  VALUES ('smoke-$(date +%s)@example.com', 'x') RETURNING id \\gset
INSERT INTO scans (user_id, status, agent, params)
  VALUES (:'id', 'queued', 'react_v1',
          jsonb_build_object('subject', '$SUBJECT', 'agent', 'react_v1',
                             'budget_usd', 0.50, 'max_calls', 5, 'max_seconds', 120))
  RETURNING user_id, id;
SQL
)
echo "  user_id=$USER_ID scan_id=$SCAN_ID"

echo "[3/5] Sending SQS message..."
docker run --rm --network agent-osint_default \
    -e AWS_ACCESS_KEY_ID=test -e AWS_SECRET_ACCESS_KEY=test \
    amazon/aws-cli:latest sqs send-message \
    --endpoint-url http://localstack:4566 --region us-east-1 \
    --queue-url http://localstack:4566/000000000000/agent-osint-scans \
    --message-body "{\"scan_id\":\"$SCAN_ID\",\"user_id\":\"$USER_ID\",\"params\":{\"subject\":\"$SUBJECT\",\"agent\":\"react_v1\",\"budget_usd\":0.50,\"max_calls\":5,\"max_seconds\":120}}"

echo "[4/5] Waiting for scan to complete (max 300s)..."
DEADLINE=$(( $(date +%s) + 300 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    STATUS=$(docker compose exec -T postgres psql -U app -d agent_osint -tA \
        -c "SELECT status FROM scans WHERE id='$SCAN_ID';")
    echo "  status=$STATUS"
    if [ "$STATUS" = "completed" ]; then
        echo "[5/5] PASS — scan completed."
        docker compose exec -T postgres psql -U app -d agent_osint -tA \
            -c "SELECT s3_key, total_cost_usd FROM scans WHERE id='$SCAN_ID';"
        exit 0
    fi
    if [ "$STATUS" = "failed" ]; then
        echo "[5/5] FAIL — scan failed."
        docker compose exec -T postgres psql -U app -d agent_osint -tA \
            -c "SELECT error_message FROM scans WHERE id='$SCAN_ID';" >&2
        docker compose logs worker | tail -50 >&2
        exit 1
    fi
    sleep 5
done
echo "[5/5] FAIL — timeout waiting for scan." >&2
docker compose logs worker | tail -100 >&2
exit 2
```

Make it executable: `chmod +x scripts/smoke_test.sh`

- [ ] **Step 12.2: Update the Makefile `smoke` target**

In `Makefile`, replace the placeholder `smoke:` target with:

```makefile
smoke:
	./scripts/smoke_test.sh
```

- [ ] **Step 12.3: Provision a real `.env`**

Run: `cp .env.example .env`
Fill in `OPENAI_API_KEY`, `XAI_API_KEY`, `APIFY_TOKEN` with your real values.

> **WARNING:** the smoke test runs a real agent and incurs real LLM cost (~$0.10–$0.30 with the budget caps above). Skip this step if you only want to verify wiring; in that case add `--no-real-llm` handling later.

- [ ] **Step 12.4: Run the smoke test**

Run: `make smoke`
Expected output ends with `[5/5] PASS — scan completed.` and prints an `s3_key`.

If it fails:
- Worker import error → check Task 7 image build + Task 9 imports.
- Migrate didn't run → `docker compose logs migrate`; ensure Postgres healthcheck passed.
- LocalStack missing bucket/queue → check `infra/localstack/init/01_create_resources.sh` was made executable AND mounted (Task 6).
- Agent runs forever → drop `max_seconds` lower in the SQS body in `scripts/smoke_test.sh`.

- [ ] **Step 12.5: Tear down**

Run: `make down`
Expected: `Volumes pgdata removed`. Stack is gone.

- [ ] **Step 12.6: Commit**

```bash
git add scripts/smoke_test.sh Makefile
git commit -m "feat(deploy): end-to-end smoke test via docker compose"
```

---

## Phase 1 done — what you can do now

- Push an SQS message → worker runs the agent → result lands in S3 + Postgres.
- Subscribe to `redis://localhost:6379` channel `scan:{id}` → see live agent events.
- All schema changes go through Drizzle (`web-next/drizzle/migrations/`); SQLAlchemy mirror is enforced by `test_schema_parity`.

What you can't do yet: there's no UI, no auth, no SSE-to-browser bridge. Phase 2 (FastAPI SSE service + Next.js app) closes that gap by consuming the same Redis channel and serving it to a browser via SSE.

---

## Self-review notes (post-write)

Spec coverage check against `2026-04-29-aws-deployment-design.md`:

| Spec section | Phase 1 task |
|---|---|
| Worker component | Tasks 7, 9, 10 |
| RedisEventSink processor | Task 8 |
| Postgres schema | Tasks 2, 3, 4 |
| S3 layout (canonical key) | Task 9 |
| Stuck-scan sweeper | Task 11 |
| Schema parity (SQLAlchemy↔Drizzle) | Task 5 |
| `web-next` (UI, auth, Stripe) | **Phase 2** |
| `api-py` SSE service | **Phase 2** |
| ALB / VPC / RDS / ECS / IAM | **Phase 3** |
| GitHub Actions / OIDC / ECR | **Phase 3** |
| AWS Budgets / CloudWatch alarms | **Phase 3** |
| Cost summary | (informational, no task) |

No placeholders. All function/class names referenced in later tasks are defined in earlier tasks (`RedisEventSink`, `execute_scan`, `run_once`, `sweep`, `WorkerConfig`).

Type consistency check: `WorkerConfig.aws_endpoint_url` is `str | None`; `boto3.client(..., endpoint_url=None)` is valid (default behavior). `execute_scan` signature is identical between Task 9 stub and Task 10 real implementation.
