# Phase 2 UI — Design Spec

**Date:** 2026-04-29
**Author:** Leo (with Claude)
**Status:** Draft for review
**Companion:** Builds on `2026-04-29-aws-deployment-design.md` (the full AWS architecture). This spec narrows that document's "Phase 2" scope and adds the UI design decisions made during brainstorming.

## Goal

Build the user-facing layer of Agent-OSINT: a Next.js web app where authenticated users can submit scans, watch agents work live, and read final reports. Sits on top of Phase 1's worker/Redis/Postgres/SQS stack. Still local (Docker Compose); AWS deployment is Phase 3.

## Non-goals (Phase 2)

- AWS infrastructure / CDK / production deploy (that's Phase 3).
- Stripe / payments (deferred to paid launch).
- Email verification, password reset flow, 2FA, OAuth providers.
- Mobile-optimized layout (responsive but desktop-first).
- Light-mode theme.
- Cmd+K modal scan launcher (full-page new-scan form is the only entry point in Phase 2).

## Locked decisions from brainstorming

| Decision | Choice |
|---|---|
| Visual aesthetic | **E — Brutalist / High contrast.** Cream background (`#f5f4f1`), heavy black borders, monumental typography, inverted black blocks for emphasis. |
| App layout | **L2 — Left sidebar + main pane.** Persistent ~140px sidebar (logo, "+ NEW SCAN" button, scan list). Main pane shows the selected scan or new-scan form. |
| Live agent feed | **T2 — Status pill + recent tail.** One rotating black status pill at top showing the active tool call. Below: last 3 completed tool calls in monospace, fading from dark (newest) to light (oldest). |
| During-scan body | Empty `REPORT` placeholder with dashed border. Final markdown report renders here once the scan completes. |
| Findings UI | **None.** No "findings cards." The agent's final markdown report (`state.report["text"]`) is the artifact every agent produces; we just render that. |
| Tool name display | **Per-tool render functions** in `osint/worker/tool_labels.py`. Internal tool names (`apify_linkedin`, etc.) never visible. Unknown tools fall through to `Tool` label with no args. |
| New-scan form | **Full-page structured form** (not modal, not chat). Agent-aware: form re-renders agent-specific fields when the user picks a different mode chip. |
| Agent display names | **N1 — technical names** stripped of version suffixes: `ReAct`, `Lead Queue`, `Multi-agent`, `Critic`. |
| Agent param manifests | Each agent ships a `manifest.py` declaring its params (label, type, default, options, help, `advanced=True`). `GET /api/agents` returns the catalog; the form renders fields from it. |
| Auth UI | NextAuth v5 Credentials provider. Sign-up gated by `allowed_emails` table (Phase 1 schema). Brutalist sign-in/sign-up pages. |
| Show-steps drawer | **Inline expansion below the report.** Each row collapsed to one line; click to reveal full args + truncated tool response. "↓ Download full response" link to fetch un-truncated payload from S3. No modal. |
| Empty state | First-time user sees welcome screen with a single `+ NEW SCAN` CTA. |
| Error state | Deep-red meta strip, white card with red border, retry button. Tool-call timeline still expandable. |
| Live progress source | Layer 2 SSE (worker → Redis pub/sub → FastAPI `EventSourceResponse` → browser). LLM token streaming (Layer 1) is not in scope for Phase 2. |

---

## Architecture (Phase 2 additions)

Phase 1 already runs Postgres + Redis + LocalStack + worker. Phase 2 adds:

```
                    ┌──────────────────┐
                    │   Browser (user) │
                    └────────┬─────────┘
                             │ HTTPS (locally: http)
                             ▼
                    ┌──────────────────┐
                    │  ALB / nginx     │  (locally: nothing — direct port mapping)
                    └────┬──────┬──────┘
              /api/*    │      │  /*  (everything else)
                ▼              ▼
       ┌──────────────────┐  ┌──────────────────────┐
       │ FastAPI svc      │  │ Next.js svc          │
       │ (Fargate, 1)     │  │ (Fargate, 1)         │
       │ - SSE stream     │  │ - UI + auth          │
       │ - GET /api/agents│  │ - NextAuth Credentials│
       │ - JWT verify     │  │ - submit scan (SQS)  │
       │ - GET /api/scans │  │                      │
       └────┬────────┬────┘  └───┬──────────┬───────┘
            │        │           │          │
            ▼        ▼           ▼          ▼
        ┌──────┐  ┌──────────┐         ┌─────┐
        │Redis │  │ Postgres │◀────────│ SQS │      (already exists from Phase 1)
        └──┬───┘  └──────────┘         └──┬──┘
           │ ▲         ▲                  │ pulls
           │ │         │                  ▼
           │ └─────────┴───────────┌──────────────┐
           │  PUBLISH              │ Worker       │      (Phase 1 — Phase 2 adds
           │                       │  + tool_labels│      `tool_labels` enrichment
           │                       │  + manifests │      and `manifests` catalog)
           │                       └──────┬───────┘
           │                              │
           │                              ▼
           │                          ┌──────┐
           │                          │  S3  │
           │                          └──────┘
           │
       (FastAPI subscribes to "scan:{id}" channels)
```

Two new services in the local Compose stack: **`api-py`** (FastAPI) and **`web-next`** (Next.js).

### `api-py` (FastAPI, Python 3.11)

- **Framework:** FastAPI + `sse-starlette` for streaming. Uvicorn worker.
- **Endpoints (Phase 2):**
  - `GET /api/stream/scans/{scan_id}` — SSE stream. Verifies NextAuth JWT cookie, asserts `scans.user_id == session.user.id`, replays Redis history list `scan:{id}:events` oldest-first, then subscribes to `scan:{id}` channel and forwards. Closes on terminal event.
  - `GET /api/scans/{scan_id}` — returns scan row + 1-hour pre-signed S3 URL for the result JSON. Used for resume-after-refresh and the final report fetch.
  - `GET /api/agents` — returns the catalog of all agents' manifests (display name, description, estimated duration, param schema). Used by the new-scan form.
  - `GET /healthz` — readiness probe.
- **Layout in repo:** new `osint/api/` package with `app.py`, `routes/`, `dependencies.py` (e.g., `current_user` JWT-verifying dependency).
- **Auth:** verifies NextAuth-signed JWT (HS256, shared `NEXTAUTH_SECRET` from env) using `python-jose`. Reads cookie `__Secure-next-auth.session-token` (or the un-prefixed local-dev variant).

### `web-next` (Next.js 15, TypeScript)

- **Framework:** Next.js 15 App Router, Tailwind, shadcn/ui base layer. Hosted as a Node container running `next start`.
- **Auth:** NextAuth v5 Credentials provider. Sessions persisted in Postgres via `@auth/drizzle-adapter`. JWT session strategy. Sign-up rejects emails not in `allowed_emails`.
- **Routes:**
  - `/auth/signin`, `/auth/signup`
  - `/scans` — list of the current user's scans (sidebar + main empty/welcome).
  - `/scans/new` — full-page agent-aware new-scan form.
  - `/scans/{id}` — scan detail (status pill + recent tail + report or empty placeholder + show-steps drawer).
- **Server Actions:**
  - `createScan(formData)` — auth-gates, validates against the manifest, INSERTs `scans` row with `status='queued'`, sends SQS message, returns `scan_id`.
- **DB access:** Drizzle ORM against the same Postgres schema declared in Phase 1's `web-next/drizzle/migrations/0000_initial.sql`.
- **Concurrency cap:** server action enforces `MAX_CONCURRENT_SCANS_PER_USER` (default 2) by counting `running`+`queued` scans for the user.

### Worker enrichment (Phase 2 additions to the existing worker)

Phase 1's worker emits `tool.started` and `tool.finished` events with raw internal tool names. Phase 2 keeps the worker container and adds two modules to it: `tool_labels.py` (described below) and the agent manifest plumbing. The `RedisEventSink` is updated to call `describe_tool_call(...)` and attach `display_label` and `arg_summary` fields to each event before publishing to Redis. Implementation:

- New module `osint/worker/tool_labels.py`:
  ```python
  TOOL_RENDERERS = {
      "web_search": ("Web search", lambda a: f'"{a["query"]}"'),
      "web_extract": (_render_extract_label, _render_urls),  # plural-aware
      "apify_linkedin": ("LinkedIn", lambda a: _slug_from_url(a["profile_url"])),
      "apify_instagram": ("Instagram", lambda a: a["username"]),
      "apify_twitter": (_twitter_label, _render_twitter),  # handle vs search mode
      "maigret": ("Username search", lambda a: a["username"]),
  }
  def describe_tool_call(name: str, args: dict) -> tuple[str, str]: ...
  ```
- Worker (or `RedisEventSink` from Phase 1) calls `describe_tool_call(...)` and adds `display_label` and `arg_summary` fields to the event payload. Browser renders `event.display_label || "Tool"`.
- Unknown tool names fall through to label `Tool` with empty `arg_summary`.

### Agent param manifests

- Base class `osint/agents/base.py:AgentManifest` with `name`, `display_name`, `description`, `estimated_duration`, `params: list[ParamField]`.
- `ParamField` has `name`, `label`, `type` (`select`, `text`, `int`, `float`, `bool`), `default`, `options` (for select), `help`, `advanced: bool` (collapsed by default), `min`/`max` (for numeric).
- One `manifest.py` per agent module, exposing a module-level `MANIFEST` constant.
- Common params (`subject`, `budget_usd`, `max_tool_calls`, `max_wall_clock_sec`) live on a base manifest every agent inherits — rendered globally, always at the top or in the global Advanced section.
- `GET /api/agents` aggregates all manifests and returns them as JSON. Next.js `/scans/new` page calls this on mount.
- Same manifest is the source of truth for the worker's `_build_config` translation, so frontend and backend can never drift on param names/types.

---

## Visual design system

### Colors (Brutalist E theme)

- Background: `#f5f4f1` (warm off-cream)
- Surface: `#ffffff` (cards, inputs)
- Foreground: `#0a0a0a` (text, borders, dividers)
- Muted: `#525252` (secondary text), `#737373` (tertiary), `#b4b3b0` (dashed borders)
- Accent — running: `#c2410c` (orange, for active state)
- Accent — finding/spotlight: `#facc15` (yellow on inverted black blocks)
- Accent — critic: `#fef3c7` background + `#a16207` text (amber alert, low contrast)
- Accent — error: `#7f1d1d` (deep red)
- Accent — success/done: `#0a0a0a` (no green; "complete" uses default foreground)

### Typography

- Sans: Inter (already loaded by Next.js default).
- Mono: JetBrains Mono — used for IDs, timestamps, tool names, args, and code blocks.
- Sizes: 9px (small caps labels), 10–11px (metadata), 12–13px (body), 14–18px (headings), 20–24px (page titles).
- Tracking: heavy letter-spacing on uppercase labels (`0.08em` to `0.18em`).
- Weight: 400/500 normal, 600 strong, 700 emphasis, 800 page titles.

### Layout primitives

- 3px solid black border separators on heavy headings; 1px solid `#d4d3d0` on internal divisions; 1px dashed `#b4b3b0` on collapsed-section affordances.
- Inverted black block (background `#0a0a0a`, foreground `#fff`/`#facc15`) marks "spotlight" content (active status pill, completion meta strip, finding callouts in error states).
- Generous bottom padding under page titles (8–10px) followed by a 3px black underline.
- Forms use 2px black input borders and 2px black submit buttons (no rounded corners).

### Sidebar

- Width 140px, white background (contrasts against cream main pane), 3px black right border.
- Logo "A-OSINT" in heavy small-caps tracking at top.
- "+ NEW" button: full-width inverted black block.
- Scan list grouped by status (`Running` / `Done`). Each scan row: subject name (one line), status + cost or running indicator (one line, smaller).
- Selected scan: light cream background (`#efeae2`), 2px black left border.

---

## Screen specs

### Sign-in (`/auth/signin`)

- Centered card, max-width 380px, 30px padding.
- Logo small-caps at top.
- Section label "SIGN IN" + page title "Welcome back." with 3px black underline.
- Email + password inputs (full-width, 2px black border, white bg).
- Submit button: full-width inverted black block, "SIGN IN →".
- Below: "No account? **Sign up**" link.
- On submission, redirect to `/scans` (or to the `next` query-param URL if present).

### Sign-up (`/auth/signup`)

- Same structure as sign-in.
- Above the form, an amber callout: `Invite-only. Your email must be on the allowed list.` (Background `#fef3c7`, 3px left border `#a16207`.)
- Email + password (min 12 chars) inputs.
- Submit creates user (bcrypt-hashed password, cost=12), starts NextAuth session, redirects to `/scans`.
- Server-side validation: email must be in `allowed_emails` table; otherwise 403 with friendly message.

### Scan list / dashboard (`/scans`)

- L2 layout. Sidebar shows the user's scans (running + done sections).
- Main pane:
  - **If user has zero scans:** empty welcome state with `Run your first scan.` heading + paragraph + `+ NEW SCAN` button.
  - **Otherwise:** main pane shows the most recently created scan (i.e., redirect to `/scans/{latest_id}`), or a similarly minimal "Pick a scan from the sidebar" hint if the user prefers an explicit empty selection.
- Sidebar pagination/filter is out of scope for Phase 2 (handle later when user has 50+ scans).

### New-scan form (`/scans/new`)

- L2 layout. Main pane is the form.
- Sections (top to bottom):
  1. Page label "NEW SCAN" + title "Investigate someone." (3px black underline).
  2. **Subject** text input (always shown; common field).
  3. **Mode** chip selector — one chip per agent, labeled with the N1 display names (`ReAct`, `Lead Queue`, `Multi-agent`, `Critic`). Selected chip is inverted black; others are bordered. Below the chips, a single-line description of the selected agent (from manifest `description`).
  4. **Agent-specific section** — a white card with 2px black border. Header label like "REACT SETTINGS". Renders fields from the selected agent's manifest in declaration order. Field types:
     - `select`: dropdown OR chip group depending on options count (≤4 → chips, >4 → dropdown).
     - `text`: text input.
     - `int` / `float`: number input with min/max.
     - `bool`: checkbox.
     - Fields with `advanced=True` live inside a nested collapsible (e.g., "▸ CRITIC TUNING").
  5. **Global advanced** collapsible: budget, max tool calls, max wall-clock seconds (the common-base manifest fields).
  6. **Submit** row: black "RUN SCAN →" button on the left + estimated duration/cost on the right (from manifest's `estimated_duration` field).
- Server Action: validates against manifest types (Pydantic), INSERTs scan row, sends SQS message, redirects to `/scans/{scan_id}`.

### Scan detail — running (`/scans/{id}`)

- L2 layout. Sidebar shows the scan with a `running` indicator.
- Main pane:
  - Page header: small `SCAN · {short_id}` label, large subject name, optional one-line goal in muted text.
  - **Status pill**: inverted black block, full-width. Pulsing `#facc15` dot + tool-name small-caps + primary arg in JetBrains Mono. Right-aligned: `7 SRC · 0:42 · $0.18`. Updates as the active tool changes.
  - **Recent tail**: 3 lines below the pill, monospace, opacity 1.0 → 0.75 → 0.5 (newest to oldest). Each line: `+0:08 · Web search "query" → 12 results`.
  - **Empty REPORT placeholder**: dashed-border box with subtitle "Will appear when investigation completes…".
  - **Footer toggle**: `▸ SHOW STEPS · 7 ACTIONS · 1 CRITIC` (dashed top border, muted small-caps). Click to expand the inline drawer.
- Live updates via `EventSource('/api/stream/scans/{scan_id}')`:
  - On `tool.started` / `tool.finished` events: update status pill (start) and prepend to recent tail (finish), pushing oldest tail entry to the next opacity tier or off-screen.
  - On `critic.*` events: brief amber flash on the recent-tail line (1s) then fades; full critic message visible in the show-steps drawer.
  - On `scan.completed`: switch to done view (see below).
  - On `scan.failed`: switch to error view.

### Scan detail — done

- Status pill replaced by a meta strip: `● COMPLETE · 1:38 · $0.31 · 11 TOOL CALLS · ↓ DOWNLOAD .MD`.
- Body: rendered Markdown report from `state.report["text"]`.
  - Headings styled with brutalist treatment: 14px bold, 0.04em letter-spacing, uppercase, 2px black bottom border, 6px gap to body.
  - Paragraphs 13px, line-height 1.55, color `#1f1f1f`.
  - `<code>` elements: small `#efeae2` background, 1px padding.
  - `<ul>` / `<ol>`: 18px left padding.
  - Citation references (`[1]`, `[2]`) link to a sources block at the bottom of the report.
- Footer: `▸ SHOW STEPS · 11 TOOL CALLS` toggle remains.

### Scan detail — show-steps drawer (expanded)

- Replaces the closed footer toggle with `▾ STEPS · 11 ACTIONS · 1 CRITIC` and renders the timeline below it.
- Each row:
  - Collapsed: timestamp (mono, muted) + display label (bold) + arg_summary (mono, muted) + result summary (muted) + `▸` chevron.
  - Expanded: same top row + a black code-block panel below showing full args + truncated tool response (~500 chars) + a `↓ DOWNLOAD FULL RESPONSE` link.
- Critic events: amber background (`#fef3c7`) inline, no expansion (the message itself is short).
- Active event (during running): orange tint, no chevron (nothing to expand yet).

### Scan detail — error

- Same layout as done state, but:
  - Meta strip is deep red (`#7f1d1d` background, white text). Includes `↻ RETRY` button on the right.
  - Body: a white card with 2px red border. `ERROR` small-caps label + the error message in JetBrains Mono + a friendly explanation paragraph.
  - Show-steps timeline still works — shows tool calls before the failure.
- Retry submits the same params as a new scan and navigates to the new scan's URL.

### Empty state (no scans yet)

- Sidebar: "+ NEW" button + "No scans yet" italic muted text under "SCANS" header.
- Main pane (vertically left-justified, top-third):
  - Small-caps "WELCOME" label.
  - Heading `Run your first scan.` (3px black underline).
  - Single explanation paragraph (~80 words) about what the app does and what to expect.
  - `+ NEW SCAN` button.

---

## Data flow (Phase 2 specifics)

A user submits a scan from `/scans/new`:

1. Browser POSTs the form to `createScan` Server Action.
2. `createScan`:
   - Reads NextAuth session; rejects if unauthenticated.
   - Validates form data against the selected agent's manifest (Pydantic).
   - Counts the user's `running`+`queued` scans; rejects if `>= MAX_CONCURRENT_SCANS_PER_USER`.
   - INSERTs scan row (`status='queued'`, `agent=<internal_name>`, `params=<jsonb>`).
   - Sends SQS message `{scan_id, user_id, params}`.
   - Returns `scan_id`. Server Action redirects to `/scans/{scan_id}`.
3. `/scans/{scan_id}` server-renders the page with `<ProgressStream>` mounted.
4. `<ProgressStream>` opens `EventSource('/api/stream/scans/{scan_id}')`.
5. FastAPI verifies the JWT, asserts ownership, replays Redis history, subscribes to live events.
6. Worker (Phase 1) picks up the SQS message, runs the agent. Each `structlog` event is enriched by the Phase 2 `tool_labels` module, then published to Redis. FastAPI forwards to the browser.
7. Browser updates the status pill + recent tail in real time.
8. When the worker emits `scan.completed` (or `scan.failed`), FastAPI closes the SSE connection. Browser fetches `GET /api/scans/{id}` to get the final state + a pre-signed S3 URL for the report Markdown. Browser renders the markdown.

### Auth-aware API contracts

Every API endpoint that returns scan data:

```python
async def stream_scan(scan_id: UUID, user: User = Depends(current_user)):
    scan = await db.get_scan(scan_id)
    if scan is None: raise HTTPException(404)
    if scan.user_id != user.id: raise HTTPException(403)
    # ...
```

403 vs 404 returns the same response body shape so a malicious user can't differentiate "exists, not yours" from "doesn't exist."

---

## Repository layout (Phase 2 additions)

```
Agent-OSINT/
├── osint/
│   ├── agents/
│   │   ├── base.py                       # NEW: AgentManifest, ParamField
│   │   ├── react_v1/manifest.py          # NEW
│   │   ├── leadqueue_v2/manifest.py      # NEW
│   │   ├── xai_multiagent_v1/manifest.py # NEW
│   │   └── critic_react_v3/manifest.py   # NEW
│   ├── api/                              # NEW: FastAPI app
│   │   ├── __init__.py
│   │   ├── app.py
│   │   ├── dependencies.py               # current_user JWT verifier
│   │   └── routes/
│   │       ├── stream.py                 # GET /api/stream/scans/{id}
│   │       ├── scans.py                  # GET /api/scans/{id}
│   │       └── agents.py                 # GET /api/agents
│   └── worker/
│       ├── tool_labels.py                # NEW: per-tool render functions
│       └── event_sink.py                 # MODIFIED: enrich events with display_label
├── web-next/
│   ├── app/
│   │   ├── auth/
│   │   │   ├── signin/page.tsx           # NEW
│   │   │   └── signup/page.tsx           # NEW
│   │   ├── scans/
│   │   │   ├── page.tsx                  # NEW: list / dashboard
│   │   │   ├── new/page.tsx              # NEW: agent-aware form
│   │   │   └── [id]/page.tsx             # NEW: scan detail
│   │   ├── api/auth/[...nextauth]/route.ts # NEW: NextAuth handler
│   │   ├── layout.tsx                    # NEW: sidebar + main pane
│   │   └── globals.css                   # NEW: Brutalist E theme
│   ├── components/
│   │   ├── Sidebar.tsx
│   │   ├── ScanList.tsx
│   │   ├── ProgressStream.tsx            # SSE consumer
│   │   ├── StatusPill.tsx
│   │   ├── RecentTail.tsx
│   │   ├── ReportMarkdown.tsx            # styled markdown renderer
│   │   ├── StepsDrawer.tsx
│   │   └── NewScanForm.tsx               # consumes /api/agents catalog
│   ├── lib/
│   │   ├── auth.ts                       # NextAuth config
│   │   ├── db.ts                         # Drizzle client
│   │   ├── sqs.ts                        # SQS client wrapper
│   │   └── api.ts                        # FastAPI client (typed)
│   ├── server-actions/
│   │   └── createScan.ts
│   ├── e2e/
│   │   └── submit-flow.spec.ts           # Playwright
│   └── Dockerfile                        # NEW
├── infra/docker/
│   └── api/Dockerfile                    # NEW: FastAPI image
├── compose.yml                           # MODIFIED: add api-py + web-next services
└── docs/superpowers/specs/
    └── 2026-04-29-phase2-ui-design.md    # this file
```

Compose adds the two new services and routes traffic via a basic nginx (or just exposes ports `3000` for Next.js and `8000` for FastAPI directly during dev).

---

## Authentication & authorization

### Sign-up

- Server Action `createUser({email, password})`:
  - Verify `email` is in `allowed_emails` (Phase 1 schema). Reject with friendly error if not.
  - Hash password with bcrypt cost=12.
  - INSERT user row.
  - Start NextAuth session (HS256 JWT) and set cookie.
  - Redirect to `/scans`.

### Sign-in

- NextAuth Credentials provider: validates email + password against `users` table (bcrypt verify).
- On success, NextAuth issues HS256 JWT signed with `NEXTAUTH_SECRET`. Cookie attributes: `httpOnly`, `Secure` (in prod), `SameSite=Lax`.

### JWT claims (shared with Python)

```json
{ "sub": "<user_id_uuid>", "email": "...", "iat": ..., "exp": ..., "jti": "..." }
```

### Python verification

- `current_user` FastAPI dependency reads cookie, verifies signature with `python-jose`, returns a `User` model. Endpoints additionally verify `scan.user_id == user.id`.

### Rate limits

- `/auth/signup` and `/auth/signin`: 10 attempts/min/IP (Redis counter on the existing ElastiCache connection — locally just the Redis container).

### Concurrency cap per user

- `MAX_CONCURRENT_SCANS_PER_USER=2` enforced in `createScan` Server Action.

### Deferred to paid launch

Email verification, password reset flow, 2FA, OAuth providers, audit log of admin actions.

---

## SSE pipe specifics (Phase 2 enrichments)

Phase 1 already publishes raw `tool.started` / `tool.finished` / `scan.started` / `scan.completed` / `scan.failed` events to Redis. Phase 2 adds:

### Worker enrichment

In `osint/worker/event_sink.py`, after the existing `RedisEventSink.__call__` builds the event payload, an enrichment step:

```python
if event_dict.get("event") in ("tool.started", "tool.finished") and "tool_name" in event_dict:
    label, arg_summary = describe_tool_call(event_dict["tool_name"], event_dict.get("args", {}))
    event_dict["display_label"] = label
    event_dict["arg_summary"] = arg_summary
```

This way the browser never needs to know the internal tool taxonomy.

### Sequence numbers

Each event carries a per-scan monotonic `seq` integer (incremented in `RedisEventSink`). Client tracks max seen `seq` and drops anything `≤ max_seen` to de-dupe across reconnects.

### Replay before subscribe

FastAPI's SSE handler:

1. `LRANGE scan:{id}:events 0 -1` (oldest-first after `reversed()`).
2. Yield each replayed event.
3. SUBSCRIBE to `scan:{id}` channel.
4. Forward live events.

Replay-then-subscribe ordering avoids in-window duplication of fast events.

### Terminal-event close

When FastAPI sees `event in ("scan.completed", "scan.failed")` it yields the event and closes the generator (which closes the SSE connection). Browser's `EventSource.onmessage` sees the terminal event, switches to done/error view, and closes the connection.

---

## Error handling & failure modes

Most Phase 1 failure modes still apply (worker OOM, Redis down, etc.). Phase 2 adds these UI-side modes:

| Failure | UI behavior |
|---|---|
| User session expires mid-scan | EventSource gets 401; client redirects to `/auth/signin?next=/scans/{id}`. Worker keeps running. |
| SSE connection drops (network blip) | EventSource auto-reconnects with backoff. New connection replays history then resumes. Brief gap in the recent tail. |
| Redis subscribe fails on FastAPI | Returns 503 to SSE. Client falls back to polling `GET /api/scans/{id}` every 5s for terminal-state detection. Recent tail freezes during fallback. |
| Worker can't reach OpenAI/xAI/Apify | Surfaces via `scan.failed` event with `error_message` from the agent. UI shows the error state. |
| Server Action `createScan` fails after DB insert (e.g. SQS send fails) | Mark scan `status='failed', error_message='enqueue_failed'`. Toast + log to console. User can retry. |
| Markdown report fetch fails (S3 down) | Show meta strip + a small error card "Report unavailable. Retry in a few seconds." Retry button refetches. |
| Manifest catalog fetch fails on `/scans/new` | Form shows a single "Could not load agents — try again" card with a retry button. No partial form. |

---

## Testing strategy

### Existing tests

Phase 1 unit and integration tests stay untouched.

### New tests (Phase 2)

| Test | Lives in | What it protects |
|---|---|---|
| `test_tool_labels` | `tests/deploy/` | Each tool's render function produces the expected label + args (web_search query, web_extract URL plurality, twitter handle vs search-query mode, linkedin slug extraction, fallback). |
| `test_manifest_catalog` | `tests/deploy/` | `GET /api/agents` returns one entry per agent with all required fields; param types match what the agent's `_build_config` expects. |
| `test_jwt_round_trip` (Python) + `test_jwt_round_trip.spec.ts` (TS) | `tests/deploy/` and `web-next/test/` | A NextAuth-signed JWT verifies in FastAPI's `current_user` dep and vice versa. Guards against `NEXTAUTH_SECRET` / algorithm drift. |
| `test_sse_endpoint` | `tests/deploy/` | Auth check, ownership check, history replay-then-subscribe order, terminal-event close. Uses `httpx.AsyncClient` against an in-process FastAPI app + fake Redis. |
| `test_create_scan_action` | `web-next/test/` | Server Action validates auth, manifest, concurrency cap, DB write, SQS send. |
| `submit-flow.spec.ts` (Playwright) | `web-next/e2e/` | One e2e: sign up (with allowed_email seeded), submit a scan with the smallest agent, see status pill update, see final report. Runs against local Docker Compose with budget capped. |

### Local dev

`make dev` brings up everything (Phase 1 services + new `api-py` + new `web-next`). User opens `http://localhost:3000`, signs in, and uses the app. `make test` runs unit + integration. `make e2e` runs Playwright.

---

## Cost & performance notes

Phase 2 is local. No new cloud costs. Phase 3 will host two more Fargate tasks (api-py + web-next) on top of Phase 1's worker — each ~$15/mo idle. ElastiCache and RDS are shared with Phase 1.

---

## Open items deferred to implementation plan

- Exact NextAuth v5 config shape (should be a TS file; needs one careful read of the v5 API surface).
- Markdown renderer choice (`react-markdown`, `mdx-js`, or roll-your-own with `marked` + DOMPurify). Whatever's simplest with citations / `<sup>` for `[1]`-style references.
- shadcn/ui component selection — decide once during the plan (we likely only need Button, Input, Label; everything else is custom-styled to match Brutalist E).
- Whether the `manifest.py` per agent should also self-validate that its declared `params` match `ScanConfig`'s actual fields (could add a unit test that imports both and diffs).
- Whether to emit `tool.started` events with `args` field or to require the worker to fetch the args from the structlog event_dict separately. Phase 1's `RedisEventSink` already passes the full event_dict; just need to confirm `tool_name` and `args` are reliably present in agent-emitted log calls.
- Sidebar pagination/filter strategy — out of scope but flag a follow-up when users hit ~50 scans.
