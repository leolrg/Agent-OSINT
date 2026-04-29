# AWS Deployment — Design Spec

**Date:** 2026-04-29
**Author:** Leo (with Claude)
**Status:** Draft for review

## Goal

Take the existing `Agent-OSINT` Python CLI (a multi-agent OSINT scanner) and deploy it to AWS as a multi-tenant web application. Internal-testing phase first (per-user accounts, no payments); designed so adding payments and scaling are config changes, not architecture changes.

## Non-goals (v1)

- Stripe integration / paid plans (the schema and architecture leave room; not built).
- Email verification, password reset flow, 2FA, OAuth providers.
- Staging environment (deploy directly to prod).
- Cross-region replication, canary deploys, performance tests, chaos engineering.
- A custom domain (use the free ALB DNS name initially).
- Automatic scan retry on failure or partial-result recovery.

## Decisions locked during brainstorming

| Decision | Choice |
|---|---|
| Compute | AWS ECS Fargate (us-east-1) |
| Auth model | Per-user accounts from day one. Invite gate via `allowed_emails` table. |
| Live progress UX | Server-Sent Events streaming structured agent events to the browser |
| Worker autoscaling | min=1, max=2 to start (cheap to dial up later) |
| Frontend | Next.js 15 (App Router) |
| IaC | AWS CDK in TypeScript |
| Domain | Free ALB-provided URL initially |
| Region | us-east-1 |
| Data layer | RDS Postgres `db.t4g.micro` + ElastiCache Redis `cache.t4g.micro` + S3 |
| CI/CD | GitHub Actions with OIDC federation |

---

## Architecture

```
                    ┌──────────────────┐
                    │   Browser (user) │
                    └────────┬─────────┘
                             │ HTTPS
                             ▼
                    ┌──────────────────┐
                    │  ALB (us-east-1) │
                    └────┬──────┬──────┘
              /api/*    │      │  /*  (everything else)
                ▼              ▼
       ┌──────────────────┐  ┌──────────────────────┐
       │ FastAPI svc      │  │ Next.js svc          │
       │ (Fargate, 1)     │  │ (Fargate, 1)         │
       │ - SSE stream     │  │ - UI + auth          │
       │ - GET /api/scans │  │ - NextAuth           │
       │ - JWT verify     │  │ - submit scan (SQS)  │
       │                  │  │ - Stripe (later)     │
       └────┬────────┬────┘  └───┬──────────┬───────┘
            │        │           │          │
            ▼        ▼           ▼          ▼
        ┌──────┐  ┌──────────┐         ┌─────┐
        │Redis │  │ Postgres │◀────────│ SQS │
        │(pub/ │  │ (RDS)    │         │     │
        │ sub) │  └──────────┘         └──┬──┘
        └──┬───┘       ▲                  │ pulls
           │ ▲         │                  ▼
           │ │         │           ┌──────────────┐
           │ └─────────┴───────────│ Worker svc   │
           │  PUBLISH              │ (Fargate,    │
           │                       │  min=1 max=2)│
           │                       │ - run agents │
           │                       │ - writes S3  │
           │                       └──────┬───────┘
           │                              │
           │                              ▼
           │                          ┌──────┐
           │                          │  S3  │
           │                          └──────┘
           │
       (FastAPI subscribes to "scan:{id}" channels)
```

Three Fargate services in one ECS cluster:

1. **`web-next`** — Next.js (UI, NextAuth sessions, Stripe routes when added). Reads/writes Postgres for users/sessions/scans. Submits new scans by inserting a row + sending an SQS message.
2. **`api-py`** — Python FastAPI. Two jobs only: (a) verify Next.js-issued session JWTs and serve the SSE progress stream by subscribing to Redis, (b) any Python-only endpoints needed later. Does NOT run agents.
3. **`worker-py`** — Python. Pulls from SQS, runs the agent (`osint.run.run_scan(...)`), publishes events to Redis, writes results to S3 + Postgres.

Shared backing services: RDS Postgres (Multi-AZ off for v1), ElastiCache Redis (single node), SQS queue, S3 bucket, Secrets Manager, CloudWatch Logs.

### Why split FastAPI from Next.js

Next.js has no good native SSE story for long-lived connections behind its serverless-style runtime. FastAPI's `sse-starlette` handles it natively in ~10 lines. The split also keeps every Python import (existing `osint.*` package, `structlog`, `pydantic` schemas) on the Python side without bridging into TypeScript.

### Why split worker from API

The API tier needs to stay snappy and respond to many SSE clients; worker tasks burn CPU on agent runs for 3–60 minutes. Different scaling profiles, different failure modes — they shouldn't share a process.

---

## Components

### `web-next` (Next.js, TypeScript)

- **Framework:** Next.js 15 App Router. Tailwind + shadcn/ui. Hosted in a Node container running `next start` (not the standalone serverless adapter — we want a long-lived process).
- **Auth:** NextAuth v5 with Credentials provider (email + password). Sessions persisted in Postgres via `@auth/drizzle-adapter`. JWT session strategy so the Python tier can verify the same token.
- **DB access:** Drizzle ORM. Drizzle owns migrations for `users`, `sessions`, `scans`, `scan_runs`, `allowed_emails`. Python uses SQLAlchemy against the same tables; Drizzle migrations are the source of truth.
- **Submitting a scan:** Server Action inserts a `scans` row (status=`queued`), sends `{scan_id, user_id, params}` to SQS, returns `scan_id`. Client redirects to `/scans/{scan_id}`.
- **SSE consumption:** The scan detail page opens an `EventSource('/api/stream/scans/{id}')` against the FastAPI tier (path-routed by ALB). Next.js itself never proxies the stream.
- **Stripe (deferred):** When billing turns on, Stripe webhooks land at a Next.js route `/api/webhooks/stripe`. No Python involvement.

### `api-py` (FastAPI, Python 3.11)

- **Framework:** FastAPI + `sse-starlette` for the streaming endpoint. Uvicorn worker.
- **Endpoints (minimal v1):**
  - `GET /api/stream/scans/{scan_id}` — authenticates via NextAuth JWT (shared `NEXTAUTH_SECRET`), checks `scans.user_id == session.user.id`, then `SUBSCRIBE`s to Redis channel `scan:{scan_id}` and forwards every message as an SSE `data:` frame. Closes the stream when it sees a terminal event (`scan.completed` or `scan.failed`).
  - `GET /api/scans/{scan_id}` — returns the latest scan row + signed S3 URL for the result JSON. Used for resume-after-refresh and final-report rendering.
  - `GET /healthz` — readiness probe.
- **JWT verification:** NextAuth signs JWTs with `NEXTAUTH_SECRET` (HS256). FastAPI verifies with `python-jose` against the same secret pulled from Secrets Manager at boot.

### `worker-py` (Python 3.11)

- **Loop:** Long-poll SQS (`WaitTimeSeconds=20`), pull one message, run the scan, ack on success.
- **Concurrency:** **One scan per worker container.** Don't multiplex; scale out by adding tasks. Per-scan structlog context and per-scan Redis channels stay clean this way.
- **Running the scan:** Calls `osint.run.run_scan(...)` directly — no subprocess, no shell. The worker process IS the agent process.
- **Event emission:** `RedisEventSink` adapter wraps `structlog`. Every `log.info(...)` inside the agent → `redis.publish("scan:{id}", json.dumps(event))`. **No agent code changes** — a custom processor fans out to Redis in addition to stdout. Agents stay deployment-agnostic.
- **Visibility-timeout heartbeat:** Worker extends SQS visibility every 5 minutes during a long scan to prevent message redelivery while the worker is still healthy.
- **Result handoff:** On success, upload final scan JSON to S3 at `s3://{bucket}/scans/{user_id}/{scan_id}.json`, update `scans.status='completed'` with `s3_key` + cost rollup, publish `scan.completed` to Redis, ack the SQS message.
- **Failure handling:** Wrap the entire scan in `try/except`. On exception: update `scans.status='failed'`, write `error_message`, publish `scan.failed`, ack the message (no automatic retry — agent failures are usually deterministic). SQS dead-letter queue catches messages that crash the worker before any handling.

### Backing services (sized for internal testing)

- **RDS Postgres:** `db.t4g.micro`, 20 GB gp3, no Multi-AZ, automated backups 7-day retention.
- **ElastiCache Redis:** `cache.t4g.micro`, single node (no replication), eviction policy `noeviction`. Used for pub/sub and a small `scan:{id}:events` capped list (last 100 events for late SSE subscribers, 24h TTL).
- **SQS:** Standard queue, visibility timeout 90 min (longer than longest expected scan), DLQ after 1 redrive.
- **S3:** One bucket, server-side encryption (SSE-S3), 90-day lifecycle to S3-IA, expire at 2 years. Versioning ON; non-current versions expire after 30 days. No public access.
- **Secrets Manager:** One secret per env (`agent-osint/prod/secrets`) holding a JSON blob: `OPENAI_API_KEY`, `XAI_API_KEY`, `APIFY_TOKEN`, `NEXTAUTH_SECRET`, `DATABASE_URL`, `REDIS_URL`.

---

## End-to-end data flow for one scan

```
t=0s    User on /new-scan, fills form: name, agent, preset, goal.
        Clicks "Run scan."

t=0.1s  Browser POSTs to Next.js Server Action createScan(...).
        web-next:
          1. Reads NextAuth session, asserts user is logged in.
          2. INSERT INTO scans (user_id, status, params, created_at) → scan_id (UUID).
          3. SQS SendMessage to "agent-osint-scans": {scan_id, user_id, params}.
          4. Returns scan_id. Server Action redirects to /scans/{scan_id}.

t=0.3s  Browser loads /scans/{scan_id} (Next.js server component).
        Page renders subject metadata + empty event feed + <ProgressStream/>.

t=0.4s  ProgressStream mounts → opens EventSource('/api/stream/scans/{scan_id}').
        ALB sees /api/* → routes to api-py.
        api-py:
          1. Reads NextAuth JWT cookie, verifies signature.
          2. SELECT user_id FROM scans WHERE id=? → asserts ownership.
          3. SUBSCRIBE redis "scan:{scan_id}".
          4. Holds connection open; sse-starlette pings every 15s.

t=1s    Worker pool already had a task warm (min=1).
        worker-py:
          1. SQS ReceiveMessage long-poll returns the scan message.
          2. Sets visibility extension heartbeat (every 5 min).
          3. UPDATE scans SET status='running', started_at=now().
          4. PUBLISH "scan:{scan_id}" {"event": "scan.started", ...}.
          5. Calls osint.run.run_scan(params, event_sink=RedisEventSink(scan_id)).

t=1.0s  api-py forwards scan.started as SSE frame.
        Browser EventSource onmessage → React state update → "Running" badge.

t=1–N   Agent runs. Every structlog event fans out to:
          - stdout (CloudWatch logs)
          - Redis "scan:{scan_id}" + history list
        Examples: tool.started, tool.finished, finding.added, critic.rejected.
        Each one streams to the browser within ~50ms.
        UI renders them as a vertical timeline of cards.

t=N     Agent completes. worker-py:
          1. s3.put_object(Bucket, Key=f"scans/{user_id}/{scan_id}.json", Body=json).
          2. UPDATE scans SET status='completed', s3_key=..., total_cost_usd=...
          3. PUBLISH "scan:{scan_id}" {"event": "scan.completed", "s3_key": ...}.
          4. SQS DeleteMessage (ack).

t=N+0.05s  api-py forwards scan.completed; client closes EventSource.
           Page re-fetches GET /api/scans/{scan_id} → renders final report.
```

### Key invariants

- **Scan ownership is checked at SSE connect AND at result fetch.** A user can never read another user's stream or result by guessing the UUID.
- **SQS visibility timeout (90 min) > longest expected scan (60 min) + buffer.** Workers also extend visibility on a 5-min heartbeat as belt-and-suspenders.
- **Failures are terminal events too.** Worker `try/except` publishes `scan.failed {error_message}`; UI handles it the same way as completion.
- **Late subscribers see recent history.** API tier reads `scan:{id}:events` (last 100 events, LPUSH+LTRIM) before subscribing for new ones, then forwards in oldest-first order. Replay-then-subscribe ordering avoids in-window duplicates.

---

## Authentication & authorization

### Sign-up & sign-in

- NextAuth v5 with Credentials provider only. Bcrypt cost=12 on passwords. Never logged.
- Sign-up `/auth/signup`: email must be in `allowed_emails` (the invite gate); reject otherwise. Insert user, create session, redirect.
- Sign-in: NextAuth issues HS256 JWT, stored as `httpOnly`, `Secure`, `SameSite=Lax` cookie.

### JWT shape

```json
{
  "sub": "<user_id_uuid>",
  "email": "alice@example.com",
  "iat": 1714400000,
  "exp": 1717000000,
  "jti": "<random>"
}
```

Same `NEXTAUTH_SECRET` (32 random bytes) signs in Next.js and verifies in Python. Both pull from Secrets Manager at boot. Rotation = update secret + redeploy both services; all sessions invalidate (acceptable for internal phase).

### Authorization pattern (both services)

- Next.js Server Actions/route handlers: `const session = await auth(); if (!session?.user) throw UnauthorizedError`. All `scans` queries filter `user_id = session.user.id`.
- FastAPI: `current_user` dependency reads cookie, verifies JWT with `python-jose`. Endpoints additionally verify `scans.user_id == current_user.id`.

The cookie proves *who*. The DB query proves *what they can see*. Both checks always.

### Worker does NOT verify auth

It trusts SQS messages because `web-next` already authenticated the submitter and stamped `scan.user_id` into the message and the row. Worker only records `user_id` so downstream filters work.

### Attack surface (cheap things worth doing now)

- ALB SG: 443 from internet, 80 redirects to 443.
- Task SGs: only ALB SG can hit task ports.
- RDS/Redis SGs: only task SGs can hit them. No public access at all.
- Rate-limit `/auth/signup` and `/auth/signin` in Next.js middleware (10/min/IP via Redis counter on the same ElastiCache instance).
- `MAX_CONCURRENT_SCANS_PER_USER=2` enforced in `web-next` submit handler.

### Deferred to paid launch

Email verification, password reset, 2FA, OAuth providers, audit log of admin actions.

---

## Live progress streaming (SSE pipe)

### Worker side: `RedisEventSink` structlog processor

Added to the agent's structlog chain **only when running under the worker** (CLI runs unaffected):

```python
class RedisEventSink:
    def __init__(self, scan_id: str, redis_client):
        self.scan_id = scan_id
        self.redis = redis_client
        self.channel = f"scan:{scan_id}"
        self.history_key = f"scan:{scan_id}:events"

    def __call__(self, logger, method_name, event_dict):
        payload = json.dumps({
            "ts": time.time(),
            "level": method_name,
            **event_dict,
        })
        try:
            self.redis.publish(self.channel, payload)
            self.redis.lpush(self.history_key, payload)
            self.redis.ltrim(self.history_key, 0, 99)
            self.redis.expire(self.history_key, 86400)
        except redis.RedisError:
            pass  # never let event sink failure break the agent
        return event_dict  # pass through to other processors (stdout)
```

Properties:
- **Zero agent code changes.** Sits in structlog's chain.
- **Best-effort on Redis failure.** Agent keeps running; user sees stalled feed but scan completes and result lands in S3.
- **Capped history (last 100 events, 24h TTL).** Lets late subscribers catch up without unbounded memory.

### API side: SSE endpoint

```python
@router.get("/api/stream/scans/{scan_id}")
async def stream_scan(scan_id: UUID, user: User = Depends(current_user)):
    scan = await db.get_scan(scan_id)
    if scan.user_id != user.id:
        raise HTTPException(403)

    async def event_generator():
        # 1. Replay buffered history (oldest-first) so reload-mid-scan works.
        history = await redis.lrange(f"scan:{scan_id}:events", 0, -1)
        for raw in reversed(history):
            yield {"data": raw}
        # 2. Subscribe for new events.
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"scan:{scan_id}")
        try:
            async for msg in pubsub.listen():
                if msg["type"] != "message": continue
                data = msg["data"]
                yield {"data": data}
                event = json.loads(data)
                if event.get("event") in ("scan.completed", "scan.failed"):
                    return
        finally:
            await pubsub.unsubscribe(f"scan:{scan_id}")
            await pubsub.aclose()

    return EventSourceResponse(event_generator(), ping=15)
```

- **Replay → subscribe ordering** is intentional. Subscribe-first risks in-window duplication of a fast event.
- **`ping=15`** keeps intermediate proxies alive.
- **ALB idle timeout = 300s** so SSE doesn't drop.
- **Per-event `seq` integer** (monotonic per scan, set by worker) lets the client de-dupe across reconnects.

### Browser side: React component

```tsx
function ProgressStream({ scanId }: { scanId: string }) {
  const [events, setEvents] = useState<ScanEvent[]>([]);
  const [status, setStatus] = useState<'connecting'|'live'|'done'|'error'>('connecting');
  const seenSeq = useRef(-1);
  useEffect(() => {
    const es = new EventSource(`/api/stream/scans/${scanId}`);
    es.onopen = () => setStatus('live');
    es.onmessage = (msg) => {
      const evt = JSON.parse(msg.data);
      if (evt.seq <= seenSeq.current) return;
      seenSeq.current = evt.seq;
      setEvents(prev => [...prev, evt]);
      if (evt.event === 'scan.completed' || evt.event === 'scan.failed') {
        setStatus('done');
        es.close();
      }
    };
    es.onerror = () => setStatus('error');
    return () => es.close();
  }, [scanId]);
  return <Timeline events={events} status={status} />;
}
```

Layer 2 streaming (agent events → browser) is independent of Layer 1 (LLM token streaming). LLM `stream=True` is an internal optimization; the live UX works either way.

---

## Data layout

### Postgres schema (Drizzle owns migrations)

```sql
-- NextAuth-managed (via @auth/drizzle-adapter)
users (
  id              uuid PRIMARY KEY,
  email           text UNIQUE NOT NULL,
  password_hash   text NOT NULL,
  email_verified  timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);

sessions (
  session_token   text PRIMARY KEY,
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires         timestamptz NOT NULL
);

allowed_emails (
  email           text PRIMARY KEY,
  added_at        timestamptz NOT NULL DEFAULT now(),
  added_by        text
);

-- Application-owned
scans (
  id                uuid PRIMARY KEY,
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status            text NOT NULL CHECK (status IN ('queued','running','completed','failed')),
  agent             text NOT NULL,
  params            jsonb NOT NULL,
  s3_key            text,
  error_message     text,
  total_cost_usd    numeric(10,4),
  total_tool_calls  integer,
  created_at        timestamptz NOT NULL DEFAULT now(),
  started_at        timestamptz,
  completed_at      timestamptz
);
CREATE INDEX scans_user_created_idx ON scans (user_id, created_at DESC);
CREATE INDEX scans_status_started_idx ON scans (status, started_at);

scan_runs (
  id           uuid PRIMARY KEY,
  scan_id      uuid NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
  attempt      integer NOT NULL,
  worker_task  text,
  started_at   timestamptz NOT NULL,
  ended_at     timestamptz,
  outcome      text
);
```

- `scans` is the user-facing entity (one row per "Run scan" click).
- `scan_runs` is operational (one row per worker attempt). v1 always has exactly one run per scan; the table exists for future retry auditability and is cheap.
- `params` is `jsonb` because the agent set is fluid; presets/goals will evolve. Indexed access is rare (analytics, not hot path).

**Not in Postgres:** the actual scan result body (S3-only), the live event stream (Redis-only), billing data (deferred — when Stripe lands: add `users.stripe_customer_id`, `users.subscription_status`, `subscription_events` table).

### S3 layout

Bucket: `agent-osint-prod-results-{account_id}`.

```
s3://agent-osint-prod-results-XXXX/
  scans/
    {user_id}/
      {scan_id}.json          # canonical scan result
      {scan_id}/
        evidence/
          {sha256}.html       # cached page snapshots
          {sha256}.png        # screenshots
        logs/
          full.jsonl          # full structlog dump (debugging)
```

- Path includes `user_id` so per-user IAM conditions become trivial later.
- `{scan_id}.json` is the single source of truth for what UI renders.
- All access via 1-hour pre-signed URLs from `api-py`. No public reads.
- Lifecycle: 90 days → S3-IA, 2 years → expire. Versioning ON, non-current → 30-day expiry.

### Migrations & schema parity

- Drizzle migrations in `web-next/drizzle/migrations/` run automatically on `web-next` boot (one-shot `drizzle-kit migrate`). First container wins; later containers see migrations as already applied.
- Python uses SQLAlchemy mirrors of those tables — does NOT run migrations.
- A `tests/test_schema_parity.py` test diffs SQLAlchemy column metadata against `information_schema` in CI to catch drift.

---

## Secrets, config, and IAM

### Where each kind of config lives

| Kind | Where | Examples |
|---|---|---|
| Secrets | AWS Secrets Manager | `OPENAI_API_KEY`, `XAI_API_KEY`, `APIFY_TOKEN`, `NEXTAUTH_SECRET`, `DATABASE_URL`, `REDIS_URL` |
| Non-secret runtime config | ECS task env vars | `AWS_REGION`, `S3_BUCKET`, `SQS_QUEUE_URL`, `MAX_CONCURRENT_SCANS_PER_USER`, `LOG_LEVEL` |
| Build-time | Dockerfile / Next.js build | `NEXT_PUBLIC_*` (only the truly public) |
| Per-environment differences | CDK stack params | `dev` vs `prod` stacks (separate accounts/regions/sizing, same code) |

### Secret injection

ECS task definition uses `secrets` (not `environment`) entries mapping JSON keys to env vars:

```ts
secrets: {
  OPENAI_API_KEY: ecs.Secret.fromSecretsManager(secret, 'OPENAI_API_KEY'),
  XAI_API_KEY:    ecs.Secret.fromSecretsManager(secret, 'XAI_API_KEY'),
  // ...
}
```

ECS resolves these at task start. Container never holds AWS creds or hits Secrets Manager itself; the `task-execution-role` does it.

### IAM roles (least privilege)

- **`web-next-task-role`:** `secretsmanager:GetSecretValue` on prod secret; `sqs:SendMessage` on scans queue.
- **`api-py-task-role`:** `secretsmanager:GetSecretValue`; `s3:GetObject` on `scans/*` (for pre-signed URL generation).
- **`worker-py-task-role`:** `secretsmanager:GetSecretValue`; `sqs:ReceiveMessage`/`DeleteMessage`/`ChangeMessageVisibility`; `s3:PutObject`/`GetObject` on `scans/*`.
- **`task-execution-role`** (shared): pull from ECR, write CloudWatch logs, fetch secret to inject. Standard `AmazonECSTaskExecutionRolePolicy` + inline policy for the secret ARN.

### Network IAM

```
Internet → ALB-SG (443 from 0.0.0.0/0)
         → Task-SGs (3000 from ALB-SG only — Next.js)
                    (8000 from ALB-SG only — FastAPI)
         → RDS-SG  (5432 from web-next/api-py/worker-py SGs only)
         → Redis-SG (6379 from web-next/api-py/worker-py SGs only)
```

No bastion, no SSH. Operational shell access via `aws ecs execute-command` (auditable through SSM).

### Cost-control belt-and-suspenders

- AWS Budgets alarm at 50% / 80% / 100% of monthly cap (e.g., $200 for internal phase). Email notifications.
- CloudWatch alarm: > 5 simultaneously running worker tasks for > 10 min — catches runaway agent loops.
- Per-user concurrency cap (`MAX_CONCURRENT_SCANS_PER_USER=2`) caps blast radius from a teammate's accidental loop.

---

## Error handling & failure modes

### Worker-side

| Failure | Detection | Response | User-visible |
|---|---|---|---|
| Agent raises unhandled exception | `try/except` around `run_scan` | UPDATE `status='failed'`, publish `scan.failed`, ack | Error card |
| LLM API 429/5xx | Existing retry logic in agent | Agent retries; if exhausted → unhandled | Same |
| Scan runs > 60 min | Worker heartbeat extends visibility | Scan finishes whenever | Live feed keeps streaming |
| Worker OOM / crash | `TaskStoppedReason` in ECS; SQS visibility expires after 90 min | Stuck-scan sweeper marks `failed` | "Scan failed: worker_timeout" after ~90 min |
| Worker can't reach Redis | `try/except` swallows publish | Scan completes, only stdout | Live feed freezes; UI auto-falls-back to polling |
| Worker can't reach S3 (upload) | boto3 retry (3) | On final failure: `status='failed', error='s3_upload_failed'` | Error card |
| Worker can't reach Postgres | `pool_pre_ping` retry; persistent → exit 1, ECS replaces | Message returns to queue, picked up by next worker | Brief delay |

### Stuck-scan sweeper (operational safety net)

CloudWatch Events rule every 10 minutes → small Lambda (or cron task in `worker-py`) runs:

```sql
UPDATE scans
SET status = 'failed',
    error_message = 'worker_timeout',
    completed_at = now()
WHERE status = 'running'
  AND started_at < now() - interval '90 minutes';
```

Plus publishes `scan.failed` event for each affected scan. Without this, dead scans sit in `running` forever.

### API-side

| Failure | Detection | Response |
|---|---|---|
| api-py task crashes | ALB health check fails, ECS replaces | Client EventSource reconnects; history replay catches up |
| Redis subscribe fails | Endpoint catches `RedisError`, returns 503 | Client retries with backoff; UI polls `/api/scans/{id}` every 5s as fallback |
| Invalid scan_id | DB lookup → 404 | UI: "Scan not found" |
| Foreign-user scan_id | Ownership check → 403 (same response shape as 404) | UI: "Access denied" |

### Web-next-side

| Failure | Detection | Response |
|---|---|---|
| Submit clicked but SQS send fails (after DB insert) | Server Action `try/except` | Mark `status='failed', error_message='enqueue_failed'`; user toast |
| Submit clicked but DB insert fails | Server Action catches before SQS | Toast; nothing in flight |
| Reload `/scans/{id}` mid-scan | Page server-renders current row; SSE re-mounts | Seamless; brief visual flash |
| NextAuth session expires mid-scan | EventSource gets 401 | Client redirects to `/auth/signin`; scan continues; user sees it on return |

### Backing-service failures

| Failure | Blast radius | Recovery |
|---|---|---|
| RDS down | Submit/list/auth degraded | Manual restart, ~5–10 min RTO (Multi-AZ added at paid launch) |
| Redis down | Live progress fails; final results unaffected | UI auto-fallback to polling |
| SQS down (rare) | Submit fails; in-flight scans complete | AWS SLA |
| S3 down (rare) | Result upload fails → scans `failed` | Manual re-run |
| Single-AZ outage | Worker tasks in that AZ die | All Fargate services span 2 AZs; <5 min reschedule |

### Deliberately not handled in v1

- Automatic scan retry on failure (agent failures usually deterministic; auto-retry wastes LLM money).
- Partial-result recovery (worker crash mid-scan loses that scan).
- Streaming error backpressure (Redis pub/sub is lossy by design; history list catches reconnects up).

---

## Testing & CI/CD

### Existing tests

`tests/` of agent-level tests stays untouched. Deployment doesn't change agent logic.

### New tests (organized by what they protect)

| Test | Lives in | What it protects |
|---|---|---|
| `test_redis_event_sink` | `tests/deploy/` | Processor publishes correct shape; survives Redis failure |
| `test_worker_loop` | `tests/deploy/` | SQS → `run_scan` → S3 → DB happens in order (mock SQS/S3/DB) |
| `test_stuck_scan_sweeper` | `tests/deploy/` | Sweeper SQL marks correct rows |
| `test_jwt_round_trip` | `tests/deploy/` + `web-next/test/` | NextAuth-signed JWT verifies in FastAPI; guards secret/algorithm drift |
| `test_schema_parity` | `tests/deploy/` | SQLAlchemy metadata matches `information_schema` after Drizzle migrations |
| `test_sse_endpoint` | `tests/deploy/` | Auth check, ownership check, history replay, terminal close (httpx + fake Redis) |
| `submit_flow.spec.ts` (Playwright) | `web-next/e2e/` | One e2e: sign up, submit scan, see live feed, see final result. Runs against local Docker Compose. |

### Local dev: Docker Compose

`compose.yml` boots Postgres + Redis + LocalStack (S3+SQS) + all three services. `make dev` boots everything. End-to-end debug without touching AWS.

### CI/CD pipeline (GitHub Actions)

Three workflows in `.github/workflows/`:

**`ci.yml`** — runs on every PR and push to `main`:
1. `pytest tests/` (existing + new deploy tests)
2. `pnpm test` + `pnpm build` (Next.js typecheck + production build)
3. `pnpm playwright test` (e2e against Docker Compose)
4. `cdk synth` (catches CDK errors before deploy)
5. Lint: `ruff` (Python), `eslint` (TS)

**`deploy-prod.yml`** — runs on push to `main` after `ci.yml` passes:
1. AWS auth via OIDC (no long-lived keys)
2. Build all three Docker images, push to ECR with tag `${{ github.sha }}`
3. `cdk deploy AgentOsintProdStack --require-approval never`
4. CDK updates ECS services → rolling deploy
5. Post-deploy smoke test: `curl https://<alb>/healthz` per service

**`db-migrate.yml`** — manual (`workflow_dispatch` only):
1. Build one-shot migration container
2. ECS `RunTask` with `drizzle-kit migrate`
3. Wait for completion, fail on non-zero exit

Manual on purpose: schema migrations should be deliberate, not a `git push` side-effect.

### Deploy cadence & rollback

- First deploy: `cdk deploy` from laptop after `aws configure sso`, ~30 min.
- Subsequent: automatic on `main` merge.
- Rollback: re-run previous successful `deploy-prod.yml` via "Re-run job" — old SHA, ECS rolls back, ~3 min. Faster fallback: `aws ecs update-service --task-definition <previous-revision>` per service, ~2 min.

### Skipped for v1

Canary/blue-green deploys, performance tests, chaos engineering, container image scanning gating, synthetic monitoring. ECR `scanOnPush=true` is enabled but doesn't gate deploys until paid launch.

---

## Cost summary (rough monthly idle)

| Component | Idle cost |
|---|---|
| ALB | ~$18 |
| ECS Fargate: web-next (1 task, 0.5 vCPU / 1 GB) | ~$15 |
| ECS Fargate: api-py (1 task, 0.5 vCPU / 1 GB) | ~$15 |
| ECS Fargate: worker-py (1 task, 1 vCPU / 2 GB) | ~$30 |
| RDS Postgres `db.t4g.micro` (20 GB gp3) | ~$13 |
| ElastiCache Redis `cache.t4g.micro` | ~$13 |
| NAT Gateway (1) | ~$32 |
| S3, SQS, Secrets Manager, CloudWatch | ~$5 |
| **Total idle** | **~$140/mo** |

Plus LLM API costs (which dominate during active use): per the README, ~$0.30–0.70 per `react_v1` scan, ~$0.30–0.50 per `leadqueue_v2` scan. Dwarfs infra cost at any non-trivial usage.

NAT Gateway is the biggest line item. Documented as a known cost; revisit at paid launch if it matters (alternatives: public-subnet Fargate with no NAT, or VPC endpoints for AWS services to skip NAT for those).

---

## Scaling-up trajectory

The architecture above is what you'd run at 10,000 users. Scaling is config, not redesign:

- **Knob 1 — worker count:** raise CDK `max_capacity` from 2 → 30. `cdk deploy`. ~30s.
- **Knob 2 — DB tier:** RDS class `db.t4g.micro` → `small` → `medium`. In-place upgrade.
- **Knob 3 — task size:** task definition cpu/memory bumps. Rolling deploy.
- **Knob 4 — per-user fairness:** ~20 lines in `web-next` submit handler if one user starves others.

Stripe + paid plans land via Next.js routes when you decide to flip the switch — DB columns added, no architecture change.

---

## Repository layout

```
Agent-OSINT/
├── osint/                       # existing Python agent code (unchanged)
├── tests/                       # existing tests (unchanged)
│   └── deploy/                  # NEW — deployment-related tests
├── web-next/                    # NEW — Next.js app
│   ├── app/                     # App Router routes
│   ├── components/              # React components (Timeline, ProgressStream, ...)
│   ├── lib/                     # auth, db, sqs clients
│   ├── drizzle/migrations/      # schema migrations (source of truth)
│   ├── e2e/                     # Playwright tests
│   └── Dockerfile
├── infra/
│   ├── cdk/                     # NEW — AWS CDK app (TypeScript)
│   │   ├── bin/app.ts           # entry
│   │   ├── lib/network-stack.ts # VPC, subnets, NAT, SGs
│   │   ├── lib/data-stack.ts    # RDS, Redis, S3, SQS, Secrets
│   │   ├── lib/services-stack.ts# ECS cluster, ALB, 3 Fargate services
│   │   └── lib/observability.ts # CW alarms, Budgets
│   └── docker/
│       ├── api/Dockerfile       # FastAPI image
│       └── worker/Dockerfile    # Worker image
├── compose.yml                  # NEW — local dev (Postgres+Redis+LocalStack+services)
├── .github/workflows/           # NEW — ci.yml, deploy-prod.yml, db-migrate.yml
└── docs/superpowers/specs/2026-04-29-aws-deployment-design.md  # this file
```

---

## Open items deferred to implementation plan

- Exact CDK construct choices (e.g., `ApplicationLoadBalancedFargateService` vs hand-rolled) — pick during plan.
- Exact ECS task sizing (the table above is a starting point; may need bumping based on Next.js memory and worker scan profile).
- Whether the stuck-scan sweeper is a Lambda or a cron task in the worker container — Lambda is cleaner; defer until plan.
- Specific shadcn/ui components to use for the timeline — UX detail, not architectural.
- Migration story when we add Stripe — add a follow-up spec when that work starts.
