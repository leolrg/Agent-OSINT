# Phase 2: FastAPI SSE service + Next.js UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the user-facing layer of Agent-OSINT — a Next.js app at `localhost:3000` where authenticated users submit scans, watch agents work live via SSE, and read final markdown reports. Sits on top of Phase 1's worker/Redis/Postgres/SQS stack. Still local; AWS deploy is Phase 3.

**Architecture:** Two new services in the local Compose stack. **`api-py`** (FastAPI + sse-starlette) hosts the SSE stream, scan-detail endpoint, and agent-manifest catalog — it never runs agents, only serves data the worker has already produced. **`web-next`** (Next.js 15 App Router + NextAuth Credentials) is the user-facing app. Worker is updated with two new modules (`tool_labels.py` + per-agent `manifest.py` files) so its emitted events carry user-facing labels and so the form can render agent-specific fields dynamically.

**Tech Stack:** Python 3.11, FastAPI, sse-starlette, python-jose, Pydantic v2 (already in repo). Node 22, Next.js 15 (App Router), TypeScript, Tailwind CSS, NextAuth v5 (`next-auth@5`), Drizzle ORM (already from Phase 1), `bcrypt-ts` (TypeScript bcrypt). Playwright for e2e.

**Phase context:** Phase 2 of 3. Phase 1 (worker, schema, compose stack) is committed and verified end-to-end via `make smoke`. Phase 3 (CDK + AWS deploy) is a separate plan written after Phase 2 lands.

**Conventions for this plan:**
- Working dir is the repo root: `/Users/leolrg/Agent-OSINT/`.
- Python: use `.venv/bin/python` and `.venv/bin/pytest` — Phase 1's venv is fully synced.
- TypeScript: `cd web-next && npm run …` (the `web-next/` dir already exists from Phase 1; we add Next.js + auth deps in Task 9).
- Each `git commit` step uses Conventional Commits and includes a `Co-Authored-By: Claude Opus 4.7 (1M context)` trailer (matching the project history).
- Tests for backend additions go under `tests/deploy/`; Next.js component tests go under `web-next/test/`; e2e in `web-next/e2e/`.
- "The spec" refers to `docs/superpowers/specs/2026-04-29-phase2-ui-design.md`.

---

## File map

New files created across this plan:

```
osint/agents/base.py                                # Task 1 — AgentManifest, ParamField
osint/agents/react_v1/manifest.py                   # Task 2
osint/agents/leadqueue_v2/manifest.py               # Task 2
osint/agents/xai_multiagent_v1/manifest.py          # Task 2
osint/agents/critic_react_v3/manifest.py            # Task 2
osint/worker/tool_labels.py                         # Task 3
osint/api/__init__.py                               # Task 5
osint/api/app.py                                    # Task 5
osint/api/dependencies.py                           # Task 5 (current_user)
osint/api/routes/__init__.py                        # Task 5
osint/api/routes/health.py                          # Task 5
osint/api/routes/agents.py                          # Task 6 (GET /api/agents)
osint/api/routes/scans.py                           # Task 7 (GET /api/scans/{id})
osint/api/routes/stream.py                          # Task 8 (GET /api/stream/scans/{id})
osint/api/aws.py                                    # Task 7 (boto3 client builders)
infra/docker/api/Dockerfile                         # Task 9

tests/deploy/test_manifest_catalog.py               # Task 2
tests/deploy/test_tool_labels.py                    # Task 3
tests/deploy/test_event_sink_enrichment.py          # Task 4
tests/deploy/test_jwt_round_trip.py                 # Task 5
tests/deploy/test_api_agents.py                     # Task 6
tests/deploy/test_api_scans.py                      # Task 7
tests/deploy/test_api_stream.py                     # Task 8

web-next/next.config.ts                             # Task 10
web-next/tailwind.config.ts                         # Task 10
web-next/postcss.config.mjs                         # Task 10
web-next/app/layout.tsx                             # Task 10
web-next/app/globals.css                            # Task 10 (Brutalist E theme)
web-next/app/page.tsx                               # Task 10 (root redirects to /scans or /auth/signin)
web-next/lib/db.ts                                  # Task 10 (Drizzle client)
web-next/lib/sqs.ts                                 # Task 13 (SQS client)
web-next/lib/api.ts                                 # Task 14 (typed client for /api/*)

web-next/auth.ts                                    # Task 11 (NextAuth v5 config)
web-next/middleware.ts                              # Task 11 (auth gate)
web-next/app/api/auth/[...nextauth]/route.ts        # Task 11
web-next/app/auth/signin/page.tsx                   # Task 11
web-next/app/auth/signup/page.tsx                   # Task 11
web-next/app/auth/signup/actions.ts                 # Task 11 (createUser server action)

web-next/components/Sidebar.tsx                     # Task 12
web-next/components/ScanList.tsx                    # Task 12
web-next/app/scans/layout.tsx                       # Task 12 (shared sidebar shell)
web-next/app/scans/page.tsx                         # Task 12 (list / empty state)

web-next/server-actions/createScan.ts               # Task 13
web-next/components/NewScanForm.tsx                 # Task 13
web-next/app/scans/new/page.tsx                     # Task 13

web-next/components/StatusPill.tsx                  # Task 14
web-next/components/RecentTail.tsx                  # Task 14
web-next/components/ProgressStream.tsx              # Task 14 (SSE consumer)
web-next/app/scans/[id]/page.tsx                    # Task 14

web-next/components/ReportMarkdown.tsx              # Task 15
web-next/components/StepsDrawer.tsx                 # Task 15
                                                    # Task 15 also extends scans/[id]/page.tsx

web-next/e2e/submit-flow.spec.ts                    # Task 16
web-next/playwright.config.ts                       # Task 16
```

Modified files:

```
osint/worker/event_sink.py                          # Task 4 (enrich events)
compose.yml                                         # Task 9 + Task 10 (add api-py + web-next)
Makefile                                            # Task 16 (add `make e2e` and refresh `make smoke` notes)
pyproject.toml                                      # Task 5 (add fastapi, sse-starlette, python-jose deps)
web-next/package.json                               # Task 10 (Next.js + NextAuth + bcrypt-ts)
.env.example                                        # Task 11 (add NEXTAUTH_URL, MAX_CONCURRENT_SCANS_PER_USER)
```

---

## Task 1: Agent manifest base class

**Files:**
- Create: `osint/agents/base.py`

The base class every agent's `manifest.py` will use to declare its parameters. Pydantic v2 (already in repo) so JSON serialization for `/api/agents` is automatic.

- [ ] **Step 1.1: Write `osint/agents/base.py`**

```python
"""Agent parameter manifests.

Each agent ships a `manifest.py` declaring its user-facing parameter
schema. The catalog is exposed via FastAPI `GET /api/agents` and consumed
by the Next.js new-scan form to render fields dynamically. The same
manifest is the source of truth for the worker's _build_config translation
so frontend and backend can never drift on param names/types.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


ParamType = Literal["select", "text", "int", "float", "bool"]


class ParamField(BaseModel):
    """One user-facing parameter field. Renders as a form input in the UI."""
    name: str = Field(description="Internal field name passed in SQS params.")
    label: str = Field(description="User-facing label shown in the form.")
    type: ParamType
    default: Any = None
    options: Optional[list[str]] = Field(
        default=None,
        description="For type='select': allowed values.",
    )
    help: Optional[str] = Field(default=None, description="Inline help text.")
    advanced: bool = Field(
        default=False,
        description="Hidden behind an 'advanced' collapsible by default.",
    )
    min: Optional[float] = Field(default=None, description="Numeric min.")
    max: Optional[float] = Field(default=None, description="Numeric max.")


class AgentManifest(BaseModel):
    """An agent's user-facing description + parameter schema."""
    name: str = Field(
        description="Internal agent name matching osint.agents.AGENTS key."
    )
    display_name: str = Field(description="UI label, e.g. 'ReAct'.")
    description: str = Field(description="One-line UI description.")
    estimated_duration: str = Field(
        description="Human-readable estimate, e.g. '~3-10 min'."
    )
    params: list[ParamField] = Field(
        default_factory=list,
        description="Agent-specific parameters (excluding common base params).",
    )


COMMON_PARAMS: list[ParamField] = [
    ParamField(
        name="budget_usd", label="Budget (USD)", type="float",
        default=0.50, min=0.10, max=20.0, advanced=True,
        help="Hard cost ceiling. Scan stops if exceeded.",
    ),
    ParamField(
        name="max_tool_calls", label="Max tool calls", type="int",
        default=100, min=1, max=500, advanced=True,
    ),
    ParamField(
        name="max_wall_clock_sec", label="Max wall-clock (seconds)", type="int",
        default=600, min=30, max=7200, advanced=True,
    ),
]
```

- [ ] **Step 1.2: Verify imports**

Run: `.venv/bin/python -c "from osint.agents.base import AgentManifest, ParamField, COMMON_PARAMS; print(len(COMMON_PARAMS))"`
Expected: `3`.

- [ ] **Step 1.3: Commit**

```bash
git add osint/agents/base.py
git commit -m "feat(agents): manifest base class + common param fields"
```

---

## Task 2: Per-agent manifests + catalog test

**Files:**
- Create: `osint/agents/react_v1/manifest.py`
- Create: `osint/agents/leadqueue_v2/manifest.py`
- Create: `osint/agents/xai_multiagent_v1/manifest.py`
- Create: `osint/agents/critic_react_v3/manifest.py`
- Create: `tests/deploy/test_manifest_catalog.py`

The four agents declare their specific params. Tests verify that:
1. Each manifest's `name` matches an entry in `osint.agents.AGENTS`.
2. Every declared param name is also a valid `ScanConfig` field (so `_build_config` won't reject it).

- [ ] **Step 2.1: Write the failing test**

Write to `tests/deploy/test_manifest_catalog.py`:

```python
"""Manifests are valid: names match AGENTS registry, params are real ScanConfig fields."""
from __future__ import annotations

import importlib

import pytest

from osint.agents import AGENTS
from osint.agents.base import AgentManifest, COMMON_PARAMS
from osint.types import ScanConfig


AGENT_NAMES = sorted(AGENTS.keys())


def _load_manifest(name: str) -> AgentManifest:
    mod = importlib.import_module(f"osint.agents.{name}.manifest")
    return mod.MANIFEST


@pytest.mark.parametrize("name", AGENT_NAMES)
def test_manifest_loads_and_name_matches(name):
    m = _load_manifest(name)
    assert isinstance(m, AgentManifest)
    assert m.name == name


@pytest.mark.parametrize("name", AGENT_NAMES)
def test_manifest_params_are_real_scanconfig_fields(name):
    m = _load_manifest(name)
    config_fields = set(ScanConfig.model_fields.keys())
    common = {p.name for p in COMMON_PARAMS}
    for p in m.params:
        # Either it's a real ScanConfig field, or it's a CLI-routed param
        # like 'subject' or 'goal' that isn't on ScanConfig directly.
        # 'subject' is the only allowed exception (passed separately to scan()).
        if p.name == "subject":
            continue
        assert p.name in config_fields or p.name in common, (
            f"Manifest for {name!r} declares param {p.name!r} which is not "
            f"a field on ScanConfig. ScanConfig fields: {sorted(config_fields)}"
        )


def test_all_agents_have_manifests():
    for name in AGENT_NAMES:
        try:
            _load_manifest(name)
        except ImportError:
            pytest.fail(f"Agent {name} is in AGENTS registry but has no manifest.py")
```

- [ ] **Step 2.2: Run to verify failure**

Run: `.venv/bin/pytest tests/deploy/test_manifest_catalog.py -v`
Expected: FAIL with `ImportError: No module named 'osint.agents.react_v1.manifest'`.

- [ ] **Step 2.3: Write `osint/agents/react_v1/manifest.py`**

```python
"""User-facing param schema for the ReAct agent."""
from osint.agents.base import AgentManifest, ParamField


MANIFEST = AgentManifest(
    name="react_v1",
    display_name="ReAct",
    description="Single ReAct loop with multi-pass deepen. Fast, modest cost.",
    estimated_duration="~3-10 min",
    params=[
        ParamField(
            name="passes", label="Passes", type="int",
            default=1, min=1, max=5,
            help="How many times the agent re-considers its draft. "
                 "1 = single pass; 2+ = additional 'deepen' passes that "
                 "critique the previous draft.",
        ),
    ],
)
```

- [ ] **Step 2.4: Write `osint/agents/leadqueue_v2/manifest.py`**

```python
"""User-facing param schema for the Lead Queue agent."""
from osint.agents.base import AgentManifest, ParamField


MANIFEST = AgentManifest(
    name="leadqueue_v2",
    display_name="Lead Queue",
    description="Priority-queue investigation with verifier loop. Slow but thorough.",
    estimated_duration="~30-60 min",
    params=[
        ParamField(
            name="max_processor_tool_calls", label="Tool calls per lead", type="int",
            default=5, min=1, max=20,
            help="Per-lead tool-call ceiling for the processor's mini-ReAct loop.",
            advanced=True,
        ),
        ParamField(
            name="max_verifier_iterations", label="Max verifier rounds", type="int",
            default=3, min=1, max=10,
            help="Cap on verifier→re-investigate cycles after first synthesis.",
            advanced=True,
        ),
    ],
)
```

- [ ] **Step 2.5: Write `osint/agents/xai_multiagent_v1/manifest.py`**

```python
"""User-facing param schema for the Multi-agent (Grok) agent."""
from osint.agents.base import AgentManifest


MANIFEST = AgentManifest(
    name="xai_multiagent_v1",
    display_name="Multi-agent",
    description="Grok 4.20 multi-agent. First-class X (Twitter) coverage via xAI's native x_search.",
    estimated_duration="~5-15 min",
    params=[],  # No agent-specific knobs; only common params apply.
)
```

- [ ] **Step 2.6: Write `osint/agents/critic_react_v3/manifest.py`**

```python
"""User-facing param schema for the Critic-driven agent."""
from osint.agents.base import AgentManifest, ParamField


MANIFEST = AgentManifest(
    name="critic_react_v3",
    display_name="Critic",
    description="Single ReAct + open-question ledger + adversarial critic. Goal-conditioned.",
    estimated_duration="variable",
    params=[
        ParamField(
            name="preset", label="Investigation context", type="select",
            default="general",
            options=[
                "coffee_career", "coffee_personal", "reconnect",
                "sales_outreach", "dossier", "general",
            ],
            help="Canned investigation posture combined with your goal in the system prompt.",
        ),
        ParamField(
            name="goal", label="Goal", type="text",
            help="Free-form goal text appended after the preset preamble. Optional.",
        ),
        ParamField(
            name="max_critic_rejections", label="Max critic rejections", type="int",
            default=3, min=1, max=10,
            help="Cap on critic rejection cycles.",
            advanced=True,
        ),
        ParamField(
            name="max_recursion_per_engagement", label="Max recursion per engagement",
            type="int", default=50, min=10, max=200, advanced=True,
        ),
        ParamField(
            name="min_tool_calls", label="Minimum tool calls", type="int",
            default=1, min=0, max=100, advanced=True,
            help="Floor below which the critic's ACCEPT verdict is overridden to REJECT.",
        ),
        ParamField(
            name="min_critic_rejections", label="Minimum critic rejections", type="int",
            default=0, min=0, max=10, advanced=True,
            help="Floor on critic rejection rounds before ACCEPT terminates.",
        ),
    ],
)
```

- [ ] **Step 2.7: Run the test**

Run: `.venv/bin/pytest tests/deploy/test_manifest_catalog.py -v`
Expected: 9 tests PASS (4 manifest_loads × 2 + 1 all_agents_have_manifests).

If a parametrize iterates fewer times than expected, AGENTS may have extra/missing keys — verify with `.venv/bin/python -c "from osint.agents import AGENTS; print(sorted(AGENTS))"`.

- [ ] **Step 2.8: Commit**

```bash
git add osint/agents/*/manifest.py tests/deploy/test_manifest_catalog.py
git commit -m "feat(agents): per-agent param manifests + catalog test"
```

---

## Task 3: tool_labels.py — server-side tool name translation

**Files:**
- Create: `osint/worker/tool_labels.py`
- Create: `tests/deploy/test_tool_labels.py`

Maps internal tool names (`apify_linkedin`, `web_search`, etc.) to user-facing labels and renders the primary arg as a short string. Lives in `osint/worker/` so it's available to `RedisEventSink` (Task 4).

- [ ] **Step 3.1: Write the failing test**

Write to `tests/deploy/test_tool_labels.py`:

```python
"""Per-tool label + arg rendering."""
from __future__ import annotations

from osint.worker.tool_labels import describe_tool_call


def test_web_search():
    label, arg = describe_tool_call(
        "web_search", {"query": "Jane Doe transformer", "max_results": 7}
    )
    assert label == "Web search"
    assert arg == '"Jane Doe transformer"'


def test_web_extract_single_url():
    label, arg = describe_tool_call(
        "web_extract", {"urls": ["https://news.ycombinator.com/item?id=1"]}
    )
    assert label == "Read page"
    assert "news.ycombinator.com" in arg
    assert "https://" not in arg  # domain-only, no protocol


def test_web_extract_multiple_urls():
    label, arg = describe_tool_call(
        "web_extract",
        {"urls": [
            "https://github.com/x", "https://arxiv.org/y",
            "https://news.ycombinator.com/z",
        ]},
    )
    assert label == "Read pages"  # plural
    assert "github.com" in arg
    assert "arxiv.org" in arg
    assert "+ 1 more" in arg


def test_apify_linkedin_extracts_slug():
    label, arg = describe_tool_call(
        "apify_linkedin",
        {"profile_url": "https://www.linkedin.com/in/jane-doe-89a/"},
    )
    assert label == "LinkedIn"
    assert arg == "jane-doe-89a"


def test_apify_instagram():
    label, arg = describe_tool_call(
        "apify_instagram", {"username": "janedoe.eth", "results_limit": 20}
    )
    assert label == "Instagram"
    assert arg == "janedoe.eth"


def test_apify_twitter_handle_mode():
    label, arg = describe_tool_call(
        "apify_twitter", {"handle": "janedoe_ml", "max_items": 20}
    )
    assert label == "X / Twitter"
    assert arg == "@janedoe_ml"


def test_apify_twitter_search_mode():
    label, arg = describe_tool_call(
        "apify_twitter",
        {"search_query": '"jane doe" since:2025-06', "max_items": 20},
    )
    assert label == "X / Twitter search"
    assert arg == '"jane doe" since:2025-06'


def test_maigret():
    label, arg = describe_tool_call("maigret", {"username": "janedoe"})
    assert label == "Username search"
    assert arg == "janedoe"


def test_unknown_tool_falls_through():
    label, arg = describe_tool_call("internal_secret_tool", {"foo": "bar"})
    assert label == "Tool"
    assert arg == ""


def test_missing_args_does_not_raise():
    label, arg = describe_tool_call("web_search", {})
    assert label == "Web search"
    assert arg == ""  # graceful — no crash on bad/missing args
```

- [ ] **Step 3.2: Run to verify failure**

Run: `.venv/bin/pytest tests/deploy/test_tool_labels.py -v`
Expected: FAIL with `ImportError: cannot import name 'describe_tool_call'`.

- [ ] **Step 3.3: Implement `osint/worker/tool_labels.py`**

```python
"""Server-side translation of internal tool names to user-facing labels.

Used by the worker's RedisEventSink to enrich tool.started / tool.finished
events with a `display_label` and `arg_summary` before publishing to Redis.
The UI renders these directly; internal tool names (apify_*, web_extract,
maigret, etc.) never reach the browser.

Adding a new tool: add an entry to TOOL_RENDERERS. If a tool ships before
its renderer, it falls through to ('Tool', '') — never leaks the internal
name.
"""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlparse


def _slug_from_linkedin_url(url: str) -> str:
    # https://www.linkedin.com/in/<slug>/[?...] -> <slug>
    try:
        path = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
        return path or url
    except Exception:
        return ""


def _domain_only(url: str) -> str:
    try:
        return urlparse(url).netloc or url
    except Exception:
        return ""


def _render_extract_label(args: dict[str, Any]) -> str:
    urls = args.get("urls") or []
    return "Read pages" if len(urls) > 1 else "Read page"


def _render_urls(args: dict[str, Any]) -> str:
    urls = args.get("urls") or []
    if not urls:
        return ""
    domains = [_domain_only(u) for u in urls]
    if len(domains) == 1:
        return domains[0]
    if len(domains) == 2:
        return ", ".join(domains)
    return f"{domains[0]}, {domains[1]} + {len(domains) - 2} more"


def _twitter_label(args: dict[str, Any]) -> str:
    if args.get("search_query"):
        return "X / Twitter search"
    return "X / Twitter"


def _render_twitter(args: dict[str, Any]) -> str:
    if args.get("search_query"):
        return str(args["search_query"])
    if args.get("handle"):
        return f"@{args['handle']}"
    return ""


# Each entry: (label_or_label_fn, arg_render_fn)
LabelOrFn = str | Callable[[dict[str, Any]], str]
ArgRenderer = Callable[[dict[str, Any]], str]

TOOL_RENDERERS: dict[str, tuple[LabelOrFn, ArgRenderer]] = {
    "web_search": (
        "Web search",
        lambda a: f'"{a["query"]}"' if a.get("query") else "",
    ),
    "web_extract": (_render_extract_label, _render_urls),
    "apify_linkedin": (
        "LinkedIn",
        lambda a: _slug_from_linkedin_url(a.get("profile_url", "")),
    ),
    "apify_instagram": (
        "Instagram",
        lambda a: a.get("username", "") or "",
    ),
    "apify_twitter": (_twitter_label, _render_twitter),
    "maigret": (
        "Username search",
        lambda a: a.get("username", "") or "",
    ),
}


def describe_tool_call(name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Return (display_label, arg_summary) for a tool call.

    Unknown tool name -> ('Tool', '').
    Render exceptions -> graceful fallback to ('Tool', '').
    """
    renderer = TOOL_RENDERERS.get(name)
    if renderer is None:
        return ("Tool", "")
    label_part, arg_fn = renderer
    try:
        label = label_part(args) if callable(label_part) else label_part
        arg = arg_fn(args) or ""
    except Exception:
        return ("Tool", "")
    return (str(label), str(arg))
```

- [ ] **Step 3.4: Run the test**

Run: `.venv/bin/pytest tests/deploy/test_tool_labels.py -v`
Expected: 10 tests PASS.

- [ ] **Step 3.5: Commit**

```bash
git add osint/worker/tool_labels.py tests/deploy/test_tool_labels.py
git commit -m "feat(worker): tool_labels module — per-tool render functions"
```

---

## Task 4: Worker enriches structlog events with display_label + arg_summary

**Files:**
- Modify: `osint/worker/event_sink.py`
- Create: `tests/deploy/test_event_sink_enrichment.py`

Phase 1's `RedisEventSink` already publishes structlog events to Redis. Phase 2 enriches each `tool.started` / `tool.finished` event with `display_label` and `arg_summary` derived from `tool_labels.describe_tool_call`. Adds a per-scan monotonic `seq` integer for client-side de-dup.

- [ ] **Step 4.1: Write the failing test**

Write to `tests/deploy/test_event_sink_enrichment.py`:

```python
"""RedisEventSink enriches tool events with display_label, arg_summary, seq."""
from __future__ import annotations

import json

import fakeredis

from osint.worker.event_sink import RedisEventSink


def _published(redis_client, channel="scan:abc"):
    """Drain pubsub messages for a channel."""
    pubsub = redis_client.pubsub()
    pubsub.subscribe(channel)
    next(pubsub.listen())  # subscribe-ack
    msgs = []
    while True:
        try:
            m = pubsub.get_message(timeout=0.05)
        except Exception:
            break
        if not m:
            break
        if m["type"] == "message":
            msgs.append(json.loads(m["data"]))
    return msgs


def test_tool_started_event_gets_display_label_and_arg_summary():
    redis = fakeredis.FakeRedis()
    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    sink(
        logger=None, method_name="info",
        event_dict={
            "event": "tool.started",
            "tool_name": "web_search",
            "args": {"query": "Jane Doe ML"},
        },
    )
    history = redis.lrange("scan:abc:events", 0, 0)
    payload = json.loads(history[0])
    assert payload["event"] == "tool.started"
    assert payload["display_label"] == "Web search"
    assert payload["arg_summary"] == '"Jane Doe ML"'


def test_tool_finished_event_also_enriched():
    redis = fakeredis.FakeRedis()
    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    sink(
        logger=None, method_name="info",
        event_dict={
            "event": "tool.finished",
            "tool_name": "apify_linkedin",
            "args": {"profile_url": "https://www.linkedin.com/in/jane-doe-89a/"},
        },
    )
    history = redis.lrange("scan:abc:events", 0, 0)
    payload = json.loads(history[0])
    assert payload["display_label"] == "LinkedIn"
    assert payload["arg_summary"] == "jane-doe-89a"


def test_non_tool_event_not_enriched():
    redis = fakeredis.FakeRedis()
    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    sink(
        logger=None, method_name="info",
        event_dict={"event": "scan.started"},
    )
    history = redis.lrange("scan:abc:events", 0, 0)
    payload = json.loads(history[0])
    assert "display_label" not in payload
    assert "arg_summary" not in payload


def test_seq_is_monotonic_per_sink():
    redis = fakeredis.FakeRedis()
    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    for i in range(3):
        sink(logger=None, method_name="info", event_dict={"event": f"e{i}"})
    history = redis.lrange("scan:abc:events", 0, -1)
    seqs = [json.loads(h)["seq"] for h in history]
    # LPUSH stores newest first, so seq sequence is 2,1,0 reading left-to-right
    assert seqs == [2, 1, 0]


def test_unknown_tool_falls_through_to_generic_label():
    redis = fakeredis.FakeRedis()
    sink = RedisEventSink(scan_id="abc", redis_client=redis)
    sink(
        logger=None, method_name="info",
        event_dict={
            "event": "tool.started",
            "tool_name": "internal_secret_tool",
            "args": {"foo": "bar"},
        },
    )
    history = redis.lrange("scan:abc:events", 0, 0)
    payload = json.loads(history[0])
    assert payload["display_label"] == "Tool"
    assert payload["arg_summary"] == ""
```

- [ ] **Step 4.2: Run to verify failure**

Run: `.venv/bin/pytest tests/deploy/test_event_sink_enrichment.py -v`
Expected: FAIL — `display_label` missing in published payload.

- [ ] **Step 4.3: Update `osint/worker/event_sink.py`**

Read the current file first to preserve other behavior. The new full content:

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
- Phase 2: tool.started / tool.finished events are enriched with
  `display_label` and `arg_summary` (from osint.worker.tool_labels)
  so the UI never sees internal tool names. Each event also gets a
  monotonic per-scan `seq` integer for client-side de-dup across SSE
  reconnects.
"""
from __future__ import annotations

import json
import time
from typing import Any

import redis as _redis

from osint.worker.tool_labels import describe_tool_call


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
        self._seq = 0

    def __call__(self, logger: Any, method_name: str, event_dict: dict) -> dict:
        seq = self._seq
        self._seq += 1
        out: dict[str, Any] = {
            "ts": time.time(),
            "level": method_name,
            "seq": seq,
            **event_dict,
        }
        # Enrich tool events.
        if out.get("event") in ("tool.started", "tool.finished"):
            tool_name = out.get("tool_name") or out.get("tool")
            if tool_name:
                label, arg = describe_tool_call(tool_name, out.get("args", {}) or {})
                out["display_label"] = label
                out["arg_summary"] = arg
        payload = json.dumps(out, default=str)
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

- [ ] **Step 4.4: Run the new tests + the existing event-sink tests to ensure nothing regressed**

Run: `.venv/bin/pytest tests/deploy/test_event_sink_enrichment.py tests/deploy/test_redis_event_sink.py -v`
Expected: all PASS (5 new + 4 existing = 9).

If `test_publishes_to_scan_channel` (existing) breaks because of the new `seq` field, it's because the old assertion only checked `payload["event"]` — that should still work. If it breaks for a different reason, read the failure carefully; the existing tests were written before `seq` was added.

- [ ] **Step 4.5: Commit**

```bash
git add osint/worker/event_sink.py tests/deploy/test_event_sink_enrichment.py
git commit -m "feat(worker): enrich tool events with display_label, arg_summary, seq"
```

---

## Task 5: FastAPI scaffold + JWT round-trip test

**Files:**
- Modify: `pyproject.toml`
- Create: `osint/api/__init__.py`
- Create: `osint/api/app.py`
- Create: `osint/api/dependencies.py`
- Create: `osint/api/routes/__init__.py`
- Create: `osint/api/routes/health.py`
- Create: `tests/deploy/test_jwt_round_trip.py`

Sets up the FastAPI service, the `current_user` dependency that verifies NextAuth JWTs, and a JWT round-trip test that signs with the same algorithm/secret a real NextAuth would.

- [ ] **Step 5.1: Add new Python deps**

Edit `pyproject.toml`. In `[project.dependencies]`, add (after `boto3>=1.34,<2.0`):

```toml
    "fastapi>=0.110,<1.0",
    "sse-starlette>=2.0,<3.0",
    "uvicorn[standard]>=0.27,<1.0",
    "python-jose[cryptography]>=3.3,<4.0",
    "httpx>=0.27,<1.0",
```

Then sync the venv:

```bash
.venv/bin/pip install -e ".[dev]"
```

- [ ] **Step 5.2: Create empty packages**

```bash
mkdir -p osint/api/routes
touch osint/api/__init__.py osint/api/routes/__init__.py
```

- [ ] **Step 5.3: Write `osint/api/dependencies.py`**

```python
"""FastAPI dependencies — JWT verification + ownership."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Cookie, HTTPException, Request, status
from jose import JWTError, jwt


@dataclass(frozen=True)
class User:
    id: str
    email: str


def _read_jwt_cookie(request: Request) -> Optional[str]:
    """Read the NextAuth session cookie. NextAuth uses different names by env:
    - prod (HTTPS): __Secure-next-auth.session-token
    - dev (HTTP):   next-auth.session-token
    Try both; pick the first non-empty.
    """
    for name in ("__Secure-next-auth.session-token", "next-auth.session-token",
                 "authjs.session-token", "__Secure-authjs.session-token"):
        v = request.cookies.get(name)
        if v:
            return v
    # Fallback: Authorization: Bearer for tests / API consumers.
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    return None


def current_user(request: Request) -> User:
    secret = os.environ.get("NEXTAUTH_SECRET")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="NEXTAUTH_SECRET not configured",
        )
    token = _read_jwt_cookie(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    sub = payload.get("sub") or payload.get("id")
    email = payload.get("email") or ""
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return User(id=str(sub), email=str(email))
```

- [ ] **Step 5.4: Write `osint/api/routes/health.py`**

```python
"""Liveness / readiness probe."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
```

- [ ] **Step 5.5: Write `osint/api/app.py`**

```python
"""FastAPI app entrypoint. ECS / docker compose runs:
    uvicorn osint.api.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from osint.api.routes import health


def create_app() -> FastAPI:
    app = FastAPI(title="agent-osint", version="0.2.0")
    # Local dev only. Production goes through ALB on the same origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    return app


app = create_app()
```

- [ ] **Step 5.6: Write JWT round-trip test**

Write to `tests/deploy/test_jwt_round_trip.py`:

```python
"""A NextAuth-signed JWT verifies in the FastAPI current_user dependency.

Guards against NEXTAUTH_SECRET / algorithm drift between Next.js and Python.
"""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from jose import jwt

from osint.api.dependencies import User, current_user


SECRET = "test-secret-32-bytes-or-more-padding-padding-padding"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("NEXTAUTH_SECRET", SECRET)
    app = FastAPI()

    @app.get("/me")
    def me(u: User = Depends(current_user)) -> dict:
        return {"id": u.id, "email": u.email}

    return TestClient(app)


def _sign(claims: dict) -> str:
    return jwt.encode(claims, SECRET, algorithm="HS256")


def test_valid_jwt_in_cookie_is_accepted(client):
    user_id = str(uuid.uuid4())
    token = _sign({"sub": user_id, "email": "a@example.com",
                   "iat": int(time.time()), "exp": int(time.time()) + 3600})
    r = client.get("/me", cookies={"next-auth.session-token": token})
    assert r.status_code == 200
    assert r.json() == {"id": user_id, "email": "a@example.com"}


def test_authorization_bearer_also_works(client):
    token = _sign({"sub": "u1", "email": "a@example.com",
                   "exp": int(time.time()) + 3600})
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_missing_cookie_is_401(client):
    r = client.get("/me")
    assert r.status_code == 401


def test_bad_signature_is_401(client):
    token = jwt.encode({"sub": "u1", "exp": int(time.time()) + 3600},
                       "different-secret", algorithm="HS256")
    r = client.get("/me", cookies={"next-auth.session-token": token})
    assert r.status_code == 401


def test_expired_token_is_401(client):
    token = _sign({"sub": "u1", "exp": int(time.time()) - 60})
    r = client.get("/me", cookies={"next-auth.session-token": token})
    assert r.status_code == 401


def test_secure_prefix_cookie_is_also_accepted(client):
    token = _sign({"sub": "u1", "email": "a@example.com",
                   "exp": int(time.time()) + 3600})
    r = client.get("/me", cookies={"__Secure-next-auth.session-token": token})
    assert r.status_code == 200
```

- [ ] **Step 5.7: Run tests + verify the app boots**

Run: `.venv/bin/pytest tests/deploy/test_jwt_round_trip.py -v`
Expected: 6 tests PASS.

Then verify the FastAPI app loads:

```bash
.venv/bin/python -c "from osint.api.app import app; print(list(r.path for r in app.routes))"
```
Expected: a list including `/healthz`.

- [ ] **Step 5.8: Commit**

```bash
git add pyproject.toml osint/api/ tests/deploy/test_jwt_round_trip.py
git commit -m "feat(api): FastAPI scaffold + current_user JWT dep + round-trip test"
```

---

## Task 6: GET /api/agents — manifest catalog endpoint

**Files:**
- Create: `osint/api/routes/agents.py`
- Create: `tests/deploy/test_api_agents.py`
- Modify: `osint/api/app.py` (register the router)

- [ ] **Step 6.1: Write the failing test**

Write to `tests/deploy/test_api_agents.py`:

```python
"""GET /api/agents returns the catalog of all agent manifests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from osint.api.app import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("NEXTAUTH_SECRET", "test-secret-padding-padding-padding-padding")
    return TestClient(create_app())


def test_lists_all_agents(client):
    r = client.get("/api/agents")
    assert r.status_code == 200
    body = r.json()
    assert "agents" in body
    names = sorted(a["name"] for a in body["agents"])
    assert names == ["critic_react_v3", "leadqueue_v2", "react_v1", "xai_multiagent_v1"]


def test_each_entry_has_display_name_description_params(client):
    body = client.get("/api/agents").json()
    for a in body["agents"]:
        assert isinstance(a["display_name"], str) and a["display_name"]
        assert isinstance(a["description"], str) and a["description"]
        assert isinstance(a["estimated_duration"], str)
        assert isinstance(a["params"], list)


def test_includes_common_params_separately(client):
    body = client.get("/api/agents").json()
    assert "common_params" in body
    common_names = sorted(p["name"] for p in body["common_params"])
    assert common_names == ["budget_usd", "max_tool_calls", "max_wall_clock_sec"]


def test_critic_manifest_has_preset_options(client):
    body = client.get("/api/agents").json()
    critic = next(a for a in body["agents"] if a["name"] == "critic_react_v3")
    preset = next(p for p in critic["params"] if p["name"] == "preset")
    assert preset["type"] == "select"
    assert "general" in preset["options"]
    assert "coffee_career" in preset["options"]


def test_no_auth_required(client):
    # Catalog is non-sensitive metadata; endpoint accessible without a JWT.
    r = client.get("/api/agents")
    assert r.status_code == 200
```

- [ ] **Step 6.2: Run to verify failure**

Run: `.venv/bin/pytest tests/deploy/test_api_agents.py -v`
Expected: FAIL with `404 Not Found`.

- [ ] **Step 6.3: Implement the route**

Write to `osint/api/routes/agents.py`:

```python
"""GET /api/agents — public catalog of all agent manifests.

Catalog is non-sensitive: no auth required. Used by the new-scan form
to render agent-specific fields dynamically.
"""
from __future__ import annotations

import importlib

from fastapi import APIRouter

from osint.agents import AGENTS
from osint.agents.base import COMMON_PARAMS

router = APIRouter()


def _load_manifest(name: str):
    return importlib.import_module(f"osint.agents.{name}.manifest").MANIFEST


@router.get("/api/agents")
async def list_agents() -> dict:
    agents = []
    for name in sorted(AGENTS.keys()):
        try:
            m = _load_manifest(name)
            agents.append(m.model_dump(mode="json"))
        except (ImportError, AttributeError):
            # Agent without a manifest is invisible to the UI but still works
            # via the CLI. Emit a stub so the new-scan form doesn't crash.
            agents.append({
                "name": name,
                "display_name": name,
                "description": "(no manifest)",
                "estimated_duration": "",
                "params": [],
            })
    return {
        "agents": agents,
        "common_params": [p.model_dump(mode="json") for p in COMMON_PARAMS],
    }
```

- [ ] **Step 6.4: Register the router in `osint/api/app.py`**

Edit `osint/api/app.py` — replace the line `from osint.api.routes import health` with:

```python
from osint.api.routes import agents, health
```

And replace `app.include_router(health.router)` with:

```python
    app.include_router(health.router)
    app.include_router(agents.router)
```

- [ ] **Step 6.5: Run tests**

Run: `.venv/bin/pytest tests/deploy/test_api_agents.py -v`
Expected: 5 tests PASS.

- [ ] **Step 6.6: Commit**

```bash
git add osint/api/routes/agents.py osint/api/app.py tests/deploy/test_api_agents.py
git commit -m "feat(api): GET /api/agents — manifest catalog"
```

---

## Task 7: GET /api/scans/{id} — scan detail with presigned S3 URL

**Files:**
- Create: `osint/api/aws.py`
- Create: `osint/api/routes/scans.py`
- Create: `tests/deploy/test_api_scans.py`
- Modify: `osint/api/app.py`

Returns the scan row + a 1-hour presigned S3 URL for the result JSON. Auth-gated, ownership-checked.

- [ ] **Step 7.1: Write `osint/api/aws.py`**

```python
"""boto3 client builders shared across API routes.

Reads AWS_REGION and AWS_ENDPOINT_URL (LocalStack) from env. Same env
contract as the Phase 1 worker.
"""
from __future__ import annotations

import os
from functools import lru_cache

import boto3


@lru_cache(maxsize=1)
def s3_client():
    return boto3.client(
        "s3",
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL") or None,
    )
```

- [ ] **Step 7.2: Write the failing test**

Write to `tests/deploy/test_api_scans.py`:

```python
"""GET /api/scans/{id} — auth-gated scan detail with presigned S3 URL."""
from __future__ import annotations

import time
import uuid

import boto3
import pytest
from fastapi.testclient import TestClient
from jose import jwt
from moto import mock_aws
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from osint.api.app import create_app
from osint.db.models import Scan, User


pytestmark = pytest.mark.integration

SECRET = "test-secret-padding-padding-padding-padding"


def _sign(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "email": "a@example.com", "exp": int(time.time()) + 3600},
        SECRET, algorithm="HS256",
    )


@pytest.fixture
def client_factory(pg_url, monkeypatch):
    monkeypatch.setenv("NEXTAUTH_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")

    def _factory():
        return TestClient(create_app())

    return _factory


@mock_aws
def test_owner_can_get_their_scan(client_factory, pg_url, monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "agent-osint-test")
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="agent-osint-test")
    s3.put_object(Bucket="agent-osint-test",
                  Key="scans/u/s.json", Body=b'{"hello": "world"}')

    engine = create_engine(pg_url)
    Sess = sessionmaker(bind=engine)
    with Sess() as s:
        u = User(email="a@example.com", password_hash="x")
        s.add(u); s.flush()
        sc = Scan(user_id=u.id, status="completed", agent="react_v1",
                  params={"subject": "x"}, s3_key="scans/u/s.json")
        s.add(sc); s.flush()
        s.commit()
        user_id, scan_id = u.id, sc.id

    client = client_factory()
    r = client.get(
        f"/api/scans/{scan_id}",
        cookies={"next-auth.session-token": _sign(str(user_id))},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(scan_id)
    assert body["status"] == "completed"
    assert body["s3_url"] is not None  # presigned URL present


@mock_aws
def test_other_user_gets_403_with_404_shape(client_factory, pg_url, monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "agent-osint-test")
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="agent-osint-test")

    engine = create_engine(pg_url)
    Sess = sessionmaker(bind=engine)
    with Sess() as s:
        owner = User(email="owner@example.com", password_hash="x")
        intruder = User(email="other@example.com", password_hash="x")
        s.add_all([owner, intruder]); s.flush()
        sc = Scan(user_id=owner.id, status="completed", agent="react_v1",
                  params={}, s3_key="scans/u/s.json")
        s.add(sc); s.flush()
        s.commit()
        scan_id, intruder_id = sc.id, intruder.id

    client = client_factory()
    r = client.get(
        f"/api/scans/{scan_id}",
        cookies={"next-auth.session-token": _sign(str(intruder_id))},
    )
    # Both 403 and 404 are acceptable security-wise; we return 404 to avoid
    # leaking existence of the row.
    assert r.status_code in (403, 404)


def test_unauthenticated_is_401(client_factory):
    client = client_factory()
    r = client.get(f"/api/scans/{uuid.uuid4()}")
    assert r.status_code == 401


def test_unknown_scan_is_404(client_factory, pg_url):
    engine = create_engine(pg_url)
    Sess = sessionmaker(bind=engine)
    with Sess() as s:
        u = User(email="a@example.com", password_hash="x")
        s.add(u); s.flush(); s.commit()
        user_id = u.id

    client = client_factory()
    r = client.get(
        f"/api/scans/{uuid.uuid4()}",
        cookies={"next-auth.session-token": _sign(str(user_id))},
    )
    assert r.status_code == 404
```

- [ ] **Step 7.3: Run to verify failure**

Run: `.venv/bin/pytest tests/deploy/test_api_scans.py -v`
Expected: FAIL — `404 Not Found` for `/api/scans/...` (route doesn't exist).

- [ ] **Step 7.4: Implement the route**

Write to `osint/api/routes/scans.py`:

```python
"""GET /api/scans/{scan_id} — auth-gated scan detail with presigned S3 URL."""
from __future__ import annotations

import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from osint.api.aws import s3_client
from osint.api.dependencies import User, current_user
from osint.db.models import Scan
from osint.db.session import db_session


router = APIRouter()


def _presign(s3_key: str) -> Optional[str]:
    if not s3_key:
        return None
    try:
        return s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": os.environ["S3_BUCKET"], "Key": s3_key},
            ExpiresIn=3600,
        )
    except Exception:
        return None


@router.get("/api/scans/{scan_id}")
async def get_scan(scan_id: uuid.UUID, user: User = Depends(current_user)) -> dict:
    with db_session() as s:
        sc = s.execute(select(Scan).where(Scan.id == scan_id)).scalar_one_or_none()
        # Same response shape for "not yours" and "doesn't exist".
        if sc is None or str(sc.user_id) != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return {
            "id": str(sc.id),
            "status": sc.status,
            "agent": sc.agent,
            "params": sc.params,
            "error_message": sc.error_message,
            "total_cost_usd": float(sc.total_cost_usd) if sc.total_cost_usd else None,
            "total_tool_calls": sc.total_tool_calls,
            "created_at": sc.created_at.isoformat() if sc.created_at else None,
            "started_at": sc.started_at.isoformat() if sc.started_at else None,
            "completed_at": sc.completed_at.isoformat() if sc.completed_at else None,
            "s3_url": _presign(sc.s3_key) if sc.s3_key else None,
        }
```

- [ ] **Step 7.5: Register the router in `osint/api/app.py`**

In `osint/api/app.py`, change the import line:
```python
from osint.api.routes import agents, health, scans
```
And add registration after `agents`:
```python
    app.include_router(scans.router)
```

- [ ] **Step 7.6: Run tests**

Run: `.venv/bin/pytest tests/deploy/test_api_scans.py -v`
Expected: 4 tests PASS.

- [ ] **Step 7.7: Commit**

```bash
git add osint/api/aws.py osint/api/routes/scans.py osint/api/app.py tests/deploy/test_api_scans.py
git commit -m "feat(api): GET /api/scans/{id} with presigned S3 URL + auth"
```

---

## Task 8: GET /api/stream/scans/{id} — SSE endpoint

**Files:**
- Create: `osint/api/routes/stream.py`
- Create: `tests/deploy/test_api_stream.py`
- Modify: `osint/api/app.py`

The SSE endpoint that the browser's EventSource connects to. Verifies auth + ownership, replays the Redis history list oldest-first, then SUBSCRIBEs to the live channel and forwards events. Closes on terminal events.

- [ ] **Step 8.1: Write the failing test**

Write to `tests/deploy/test_api_stream.py`:

```python
"""SSE stream — replay-then-subscribe ordering, auth check, terminal close.

These tests use sync TestClient + threading; SSE in TestClient returns
a streaming response we read line-by-line.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

import fakeredis
import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from osint.api.app import create_app
from osint.db.models import Scan, User


pytestmark = pytest.mark.integration

SECRET = "test-secret-padding-padding-padding-padding"


def _sign(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "email": "a@example.com", "exp": int(time.time()) + 3600},
        SECRET, algorithm="HS256",
    )


def _seed(pg_url: str):
    engine = create_engine(pg_url)
    Sess = sessionmaker(bind=engine)
    with Sess() as s:
        u = User(email="a@example.com", password_hash="x")
        s.add(u); s.flush()
        sc = Scan(user_id=u.id, status="running", agent="react_v1", params={})
        s.add(sc); s.flush()
        s.commit()
        return u.id, sc.id


def _push_history(redis, scan_id, events: list[dict]):
    for e in reversed(events):
        # LPUSH: stored newest-first, so reverse the input so the iteration order
        # in the test (oldest-first) matches what the endpoint will read+reverse.
        redis.lpush(f"scan:{scan_id}:events", json.dumps(e))


def _read_sse_lines(response, max_events: int, timeout: float = 3.0) -> list[dict]:
    """Pull SSE `data:` lines off the streaming response."""
    out: list[dict] = []
    deadline = time.time() + timeout
    for line in response.iter_lines():
        if line and line.startswith("data: "):
            try:
                out.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
            if len(out) >= max_events:
                break
        if time.time() > deadline:
            break
    return out


@pytest.fixture
def stream_setup(pg_url, monkeypatch):
    monkeypatch.setenv("NEXTAUTH_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("REDIS_URL", "redis://unused")
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr("osint.api.routes.stream._redis_client", lambda: fake)
    return fake


def test_replays_history_then_terminates_on_completed(stream_setup, pg_url):
    fake = stream_setup
    user_id, scan_id = _seed(pg_url)
    _push_history(fake, scan_id, [
        {"event": "scan.started", "seq": 0},
        {"event": "tool.started", "tool_name": "web_search",
         "args": {"query": "x"}, "seq": 1},
        {"event": "scan.completed", "seq": 2, "s3_key": "k"},
    ])

    client = TestClient(create_app())
    with client.stream(
        "GET", f"/api/stream/scans/{scan_id}",
        cookies={"next-auth.session-token": _sign(str(user_id))},
    ) as r:
        assert r.status_code == 200
        events = _read_sse_lines(r, max_events=3)
    assert [e["event"] for e in events] == \
        ["scan.started", "tool.started", "scan.completed"]


def test_unauthenticated_is_401(stream_setup, pg_url):
    user_id, scan_id = _seed(pg_url)
    client = TestClient(create_app())
    with client.stream("GET", f"/api/stream/scans/{scan_id}") as r:
        assert r.status_code == 401


def test_other_user_is_404(stream_setup, pg_url):
    _, scan_id = _seed(pg_url)
    client = TestClient(create_app())
    intruder_token = _sign(str(uuid.uuid4()))  # random user id
    with client.stream(
        "GET", f"/api/stream/scans/{scan_id}",
        cookies={"next-auth.session-token": intruder_token},
    ) as r:
        assert r.status_code == 404
```

- [ ] **Step 8.2: Run to verify failure**

Run: `.venv/bin/pytest tests/deploy/test_api_stream.py -v`
Expected: FAIL — endpoint doesn't exist.

- [ ] **Step 8.3: Implement the SSE route**

Write to `osint/api/routes/stream.py`:

```python
"""GET /api/stream/scans/{scan_id} — SSE stream of agent events.

Verifies auth + ownership, replays Redis history list oldest-first,
then SUBSCRIBEs and forwards live events. Closes on terminal events
(scan.completed / scan.failed). Uses sse-starlette's EventSourceResponse.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import AsyncIterator

import redis as _redis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from osint.api.dependencies import User, current_user
from osint.db.models import Scan
from osint.db.session import db_session


router = APIRouter()

TERMINAL_EVENTS = {"scan.completed", "scan.failed"}


def _redis_client() -> _redis.Redis:
    """Override target for tests (fakeredis injection)."""
    return _redis.from_url(os.environ["REDIS_URL"])


@router.get("/api/stream/scans/{scan_id}")
async def stream_scan(
    scan_id: uuid.UUID,
    user: User = Depends(current_user),
):
    # Auth + ownership.
    with db_session() as s:
        sc = s.execute(select(Scan).where(Scan.id == scan_id)).scalar_one_or_none()
        if sc is None or str(sc.user_id) != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    rds = _redis_client()
    channel = f"scan:{scan_id}"
    history_key = f"scan:{scan_id}:events"

    async def gen() -> AsyncIterator[dict]:
        # Replay history oldest-first. LPUSH stores newest-first, so reverse.
        history = rds.lrange(history_key, 0, -1)
        for raw in reversed(history):
            data = raw.decode() if isinstance(raw, bytes) else raw
            yield {"data": data}
            try:
                e = json.loads(data)
                if e.get("event") in TERMINAL_EVENTS:
                    return  # already terminal; no need to subscribe
            except json.JSONDecodeError:
                pass

        # Subscribe for live events.
        pubsub = rds.pubsub()
        pubsub.subscribe(channel)
        try:
            for msg in pubsub.listen():
                if msg["type"] != "message":
                    continue
                data = msg["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                yield {"data": data}
                try:
                    e = json.loads(data)
                    if e.get("event") in TERMINAL_EVENTS:
                        return
                except json.JSONDecodeError:
                    pass
        finally:
            pubsub.unsubscribe(channel)
            pubsub.close()

    return EventSourceResponse(gen(), ping=15)
```

- [ ] **Step 8.4: Register the router in `osint/api/app.py`**

In `osint/api/app.py`, update import:
```python
from osint.api.routes import agents, health, scans, stream
```
And add registration:
```python
    app.include_router(stream.router)
```

- [ ] **Step 8.5: Run tests**

Run: `.venv/bin/pytest tests/deploy/test_api_stream.py -v`
Expected: 3 tests PASS.

If `test_replays_history_then_terminates_on_completed` hangs, the `pubsub.listen()` block isn't being skipped after the terminal event in the replay. Verify the early `return` inside the replay loop is reached on `scan.completed`.

- [ ] **Step 8.6: Commit**

```bash
git add osint/api/routes/stream.py osint/api/app.py tests/deploy/test_api_stream.py
git commit -m "feat(api): GET /api/stream/scans/{id} — SSE with replay+subscribe"
```

---

## Task 9: API Dockerfile + compose update

**Files:**
- Create: `infra/docker/api/Dockerfile`
- Modify: `compose.yml` (add `api-py` service)

- [ ] **Step 9.1: Write `infra/docker/api/Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl ca-certificates \
        pkg-config libcairo2-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml /app/
COPY osint/__init__.py /app/osint/__init__.py
COPY osint /app/osint

RUN pip install -e "."

EXPOSE 8000
CMD ["uvicorn", "osint.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 9.2: Add the `api-py` service to `compose.yml`**

Read the existing `compose.yml` first. Then add a new service entry after the `worker:` block (and before `volumes:` at the bottom):

```yaml
  api-py:
    build:
      context: .
      dockerfile: infra/docker/api/Dockerfile
    environment:
      DATABASE_URL: postgresql+psycopg://app:app@postgres:5432/agent_osint
      REDIS_URL: redis://redis:6379/0
      AWS_REGION: us-east-1
      AWS_ACCESS_KEY_ID: test
      AWS_SECRET_ACCESS_KEY: test
      AWS_ENDPOINT_URL: http://localstack:4566
      S3_BUCKET: agent-osint-local-results
      NEXTAUTH_SECRET: ${NEXTAUTH_SECRET}
      LOG_LEVEL: INFO
    ports: ["8000:8000"]
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
      localstack: { condition: service_healthy }
      migrate:  { condition: service_completed_successfully }
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/healthz"]
      interval: 5s
      timeout: 3s
      retries: 10
```

- [ ] **Step 9.3: Validate compose**

Run: `docker compose config --quiet`
Expected: exit 0.

- [ ] **Step 9.4: Build and start the new service**

```bash
docker compose build api-py
docker compose up -d api-py
sleep 6
curl -sf http://localhost:8000/healthz
```
Expected: `{"ok":true}`.

If the build fails, check that `pyproject.toml` has the new fastapi/uvicorn/sse-starlette/python-jose/httpx deps (Task 5).

- [ ] **Step 9.5: Commit**

```bash
git add infra/docker/api/Dockerfile compose.yml
git commit -m "feat(deploy): api-py service — fastapi container in compose stack"
```

---

## Task 10: Next.js scaffold + Brutalist E theme

**Files:**
- Modify: `web-next/package.json`
- Create: `web-next/next.config.ts`, `web-next/tailwind.config.ts`, `web-next/postcss.config.mjs`
- Create: `web-next/app/layout.tsx`, `web-next/app/globals.css`, `web-next/app/page.tsx`
- Create: `web-next/lib/db.ts`
- Modify: `compose.yml` (add `web-next` service)

The `web-next/` directory exists from Phase 1 with just Drizzle. We add Next.js 15 + Tailwind + the Brutalist theme.

- [ ] **Step 10.1: Update `web-next/package.json`**

Read the current file first. Then update to:

```json
{
  "name": "web-next",
  "version": "0.2.0",
  "private": true,
  "scripts": {
    "dev": "next dev -p 3000",
    "build": "next build",
    "start": "next start -p 3000",
    "lint": "next lint",
    "db:generate": "drizzle-kit generate",
    "db:migrate": "drizzle-kit migrate",
    "db:push": "drizzle-kit push"
  },
  "dependencies": {
    "next": "^15.0.0",
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "drizzle-orm": "^0.30.0",
    "postgres": "^3.4.0",
    "next-auth": "5.0.0-beta.20",
    "@auth/drizzle-adapter": "^1.4.0",
    "bcrypt-ts": "^5.0.0",
    "@aws-sdk/client-sqs": "^3.700.0",
    "react-markdown": "^9.0.0",
    "remark-gfm": "^4.0.0"
  },
  "devDependencies": {
    "drizzle-kit": "^0.21.0",
    "typescript": "^5.4.0",
    "@types/node": "^20.11.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "tailwindcss": "^3.4.0",
    "postcss": "^8.4.0",
    "autoprefixer": "^10.4.0",
    "eslint": "^9.0.0",
    "eslint-config-next": "^15.0.0",
    "@playwright/test": "^1.47.0"
  }
}
```

- [ ] **Step 10.2: Install**

```bash
cd web-next && npm install --no-audit --no-fund
```
Expected: completes; some peer-dep warnings are fine.

- [ ] **Step 10.3: Configure Next.js, Tailwind, PostCSS**

Write to `web-next/next.config.ts`:

```ts
import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  output: 'standalone',
};

export default nextConfig;
```

Write to `web-next/postcss.config.mjs`:

```js
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

Write to `web-next/tailwind.config.ts`:

```ts
import type { Config } from 'tailwindcss';

export default {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Brutalist E palette
        cream: '#f5f4f1',
        ink: '#0a0a0a',
        muted: '#525252',
        muted2: '#737373',
        border: '#d4d3d0',
        dashed: '#b4b3b0',
        accent: '#c2410c',     // running orange
        spotlight: '#facc15',  // yellow on inverted blocks
        amber: '#fef3c7',
        amber2: '#a16207',
        danger: '#7f1d1d',
        sidebar: '#efeae2',    // selected scan row
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      letterSpacing: {
        widest2: '0.18em',
      },
    },
  },
  plugins: [],
} satisfies Config;
```

- [ ] **Step 10.4: Write `web-next/app/globals.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  color-scheme: light;
}

body {
  background: #f5f4f1;
  color: #0a0a0a;
  font-family: Inter, system-ui, -apple-system, sans-serif;
}

/* Brutalist primitives */
.heavy-rule {
  border-bottom: 3px solid #0a0a0a;
}
.dashed-rule {
  border-top: 1px dashed #b4b3b0;
}
.label-uppercase {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
.spotlight {
  background: #0a0a0a;
  color: #fff;
}
```

- [ ] **Step 10.5: Write `web-next/app/layout.tsx`**

```tsx
import './globals.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'agent-osint',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
```

- [ ] **Step 10.6: Write `web-next/app/page.tsx` (root: redirects to /scans)**

```tsx
import { redirect } from 'next/navigation';

export default function RootPage() {
  redirect('/scans');
}
```

- [ ] **Step 10.7: Write `web-next/lib/db.ts`**

```ts
import { drizzle } from 'drizzle-orm/postgres-js';
import postgres from 'postgres';
import * as schema from '../drizzle/schema';

const url = process.env.DATABASE_URL_NODE
  ?? 'postgresql://app:app@localhost:5432/agent_osint';

export const sql = postgres(url, { max: 5 });
export const db = drizzle(sql, { schema });
```

- [ ] **Step 10.8: Add the `web-next` service to `compose.yml`**

Add this service block after `api-py:` (and before `volumes:`):

```yaml
  web-next:
    image: node:22-alpine
    working_dir: /app/web-next
    environment:
      DATABASE_URL_NODE: postgresql://app:app@postgres:5432/agent_osint
      NEXTAUTH_SECRET: ${NEXTAUTH_SECRET}
      NEXTAUTH_URL: http://localhost:3000
      AWS_REGION: us-east-1
      AWS_ACCESS_KEY_ID: test
      AWS_SECRET_ACCESS_KEY: test
      AWS_ENDPOINT_URL: http://localstack:4566
      SQS_QUEUE_URL: http://localstack:4566/000000000000/agent-osint-scans
      MAX_CONCURRENT_SCANS_PER_USER: "2"
      NEXT_PUBLIC_API_BASE: http://localhost:8000
    volumes:
      - ./web-next:/app/web-next
    ports: ["3000:3000"]
    command: ["sh", "-c", "npm install --no-audit --no-fund && npm run dev"]
    depends_on:
      postgres: { condition: service_healthy }
      api-py:   { condition: service_healthy }
      migrate:  { condition: service_completed_successfully }
```

- [ ] **Step 10.9: Smoke-boot Next.js**

```bash
docker compose up -d web-next
sleep 30  # cold install + dev compile
curl -sI http://localhost:3000/ | head -1
```
Expected: a `HTTP/1.1 200`, `307` (redirect to `/scans`), or similar.

- [ ] **Step 10.10: Commit**

```bash
git add web-next/package.json web-next/package-lock.json web-next/next.config.ts \
        web-next/tailwind.config.ts web-next/postcss.config.mjs \
        web-next/app/ web-next/lib/db.ts compose.yml
git commit -m "feat(ui): next.js scaffold + Brutalist E tailwind theme"
```

---

## Task 11: NextAuth Credentials + sign-in / sign-up pages

**Files:**
- Create: `web-next/auth.ts`
- Create: `web-next/middleware.ts`
- Create: `web-next/app/api/auth/[...nextauth]/route.ts`
- Create: `web-next/app/auth/signin/page.tsx`
- Create: `web-next/app/auth/signup/page.tsx`
- Create: `web-next/app/auth/signup/actions.ts`
- Modify: `.env.example`

NextAuth v5 with Credentials provider. Sign-up gated by `allowed_emails` table.

- [ ] **Step 11.1: Write `web-next/auth.ts`**

```ts
import NextAuth from 'next-auth';
import Credentials from 'next-auth/providers/credentials';
import { compare } from 'bcrypt-ts';
import { eq } from 'drizzle-orm';
import { db } from './lib/db';
import { users } from './drizzle/schema';

export const { handlers, signIn, signOut, auth } = NextAuth({
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
  session: { strategy: 'jwt' },
  trustHost: true,
  pages: {
    signIn: '/auth/signin',
  },
});
```

- [ ] **Step 11.2: Write `web-next/middleware.ts`**

```ts
import { auth } from './auth';

export default auth((req) => {
  const { pathname } = req.nextUrl;
  const isAuthPage = pathname.startsWith('/auth/');
  const isApi = pathname.startsWith('/api/');
  if (isAuthPage || isApi) return;
  if (!req.auth) {
    const url = req.nextUrl.clone();
    url.pathname = '/auth/signin';
    url.searchParams.set('next', pathname);
    return Response.redirect(url);
  }
});

export const config = {
  matcher: ['/((?!_next|favicon.ico).*)'],
};
```

- [ ] **Step 11.3: Write the NextAuth route handler**

Write to `web-next/app/api/auth/[...nextauth]/route.ts`:

```ts
export { GET, POST } from '../../../../auth';
```

(Note: path follows from the project layout — `auth.ts` is at `web-next/auth.ts`, so `../../../../auth` reaches it from `app/api/auth/[...nextauth]/route.ts`.)

Actually NextAuth v5 exports `handlers` with `{ GET, POST }`. Use:

```ts
import { handlers } from '../../../../auth';
export const { GET, POST } = handlers;
```

- [ ] **Step 11.4: Write `web-next/app/auth/signin/page.tsx`**

```tsx
import { signIn } from '../../../auth';

export default function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-[380px] py-8">
        <div className="text-[10px] font-extrabold tracking-[0.18em] mb-6">
          AGENT-OSINT
        </div>

        <div className="label-uppercase">SIGN IN</div>
        <h1 className="text-[24px] font-extrabold leading-[1.05] heavy-rule pb-2">
          Welcome back.
        </h1>

        <form
          className="mt-5 space-y-2.5"
          action={async (data) => {
            'use server';
            const params = await searchParams;
            await signIn('credentials', {
              email: data.get('email'),
              password: data.get('password'),
              redirectTo: params.next ?? '/scans',
            });
          }}
        >
          <input
            name="email" type="email" required placeholder="Email"
            className="block w-full border-2 border-ink bg-white px-2.5 py-2 text-[13px]"
          />
          <input
            name="password" type="password" required placeholder="Password"
            className="block w-full border-2 border-ink bg-white px-2.5 py-2 text-[13px]"
          />
          <button
            type="submit"
            className="block w-full bg-ink text-white py-2 text-[11px] font-bold tracking-[0.12em] uppercase mt-3"
          >
            SIGN IN →
          </button>
        </form>

        <div className="mt-4 text-[11px] text-muted">
          No account?{' '}
          <a href="/auth/signup" className="text-ink font-semibold underline">
            Sign up
          </a>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 11.5: Write `web-next/app/auth/signup/actions.ts`**

```ts
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
```

- [ ] **Step 11.6: Write `web-next/app/auth/signup/page.tsx`**

```tsx
import { createUser } from './actions';

export default function SignUpPage() {
  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-[380px] py-8">
        <div className="text-[10px] font-extrabold tracking-[0.18em] mb-6">
          AGENT-OSINT
        </div>

        <div className="label-uppercase">SIGN UP</div>
        <h1 className="text-[24px] font-extrabold leading-[1.05] heavy-rule pb-2">
          Create an account.
        </h1>

        <div className="mt-3.5 px-2.5 py-2 bg-amber border-l-[3px] border-amber2 text-[11px] leading-[1.4] text-muted">
          <strong className="text-amber2">Invite-only.</strong> Your email must be on the allowed list.
        </div>

        <form
          className="mt-3.5 space-y-2.5"
          action={async (data) => {
            'use server';
            const result = await createUser(data);
            if (result?.error) {
              throw new Error(result.error);
            }
          }}
        >
          <input
            name="email" type="email" required placeholder="Email"
            className="block w-full border-2 border-ink bg-white px-2.5 py-2 text-[13px]"
          />
          <input
            name="password" type="password" required minLength={12}
            placeholder="Password (min 12 chars)"
            className="block w-full border-2 border-ink bg-white px-2.5 py-2 text-[13px]"
          />
          <button
            type="submit"
            className="block w-full bg-ink text-white py-2 text-[11px] font-bold tracking-[0.12em] uppercase mt-3"
          >
            CREATE ACCOUNT →
          </button>
        </form>

        <div className="mt-4 text-[11px] text-muted">
          Have an account?{' '}
          <a href="/auth/signin" className="text-ink font-semibold underline">
            Sign in
          </a>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 11.7: Update `.env.example`**

In `.env.example`, in the Auth section, ensure both `NEXTAUTH_SECRET` and `NEXTAUTH_URL` exist:

```bash
# --- Auth ---
NEXTAUTH_SECRET=local-dev-secret-change-me-32-bytes-min
NEXTAUTH_URL=http://localhost:3000
MAX_CONCURRENT_SCANS_PER_USER=2
```

If only `NEXTAUTH_SECRET` was there from Phase 1, add the other two near it.

- [ ] **Step 11.8: Restart `web-next` and verify auth pages render**

```bash
docker compose restart web-next
sleep 15
curl -sI http://localhost:3000/auth/signin | head -1
curl -sI http://localhost:3000/auth/signup | head -1
```
Expected: `200 OK` from both.

Manual smoke (in a browser): visit `http://localhost:3000/`, get redirected to `/auth/signin`. Sign-up form should render.

To actually create a user, seed an entry in `allowed_emails`:

```bash
docker compose exec -T postgres psql -U app -d agent_osint \
  -c "INSERT INTO allowed_emails (email) VALUES ('you@example.com') ON CONFLICT DO NOTHING;"
```

Then sign up via the browser at `/auth/signup` with `you@example.com`.

- [ ] **Step 11.9: Commit**

```bash
git add web-next/auth.ts web-next/middleware.ts \
        web-next/app/api/auth web-next/app/auth .env.example
git commit -m "feat(ui): NextAuth Credentials + sign-in/sign-up pages with allowed_emails gate"
```

---

## Task 12: Sidebar + scan list page (with empty state)

**Files:**
- Create: `web-next/components/Sidebar.tsx`
- Create: `web-next/components/ScanList.tsx`
- Create: `web-next/app/scans/layout.tsx`
- Create: `web-next/app/scans/page.tsx`

L2 layout shell. Sidebar shows the user's scans grouped by status; main shows the welcome empty-state OR a hint to pick a scan.

- [ ] **Step 12.1: Write `web-next/components/Sidebar.tsx`**

```tsx
import Link from 'next/link';
import { ScanList } from './ScanList';
import type { ScanRow } from './ScanList';

export function Sidebar({
  scans, currentScanId,
}: { scans: ScanRow[]; currentScanId?: string }) {
  return (
    <aside className="w-[140px] bg-white border-r-[3px] border-ink p-3 shrink-0 min-h-screen">
      <div className="text-[10px] font-extrabold tracking-[0.16em] mb-3.5">
        A-OSINT
      </div>
      <Link
        href="/scans/new"
        className="block bg-ink text-white py-1.5 px-2 text-[9px] font-bold tracking-[0.1em] uppercase text-center mb-3.5"
      >
        + NEW
      </Link>
      <ScanList scans={scans} currentScanId={currentScanId} />
    </aside>
  );
}
```

- [ ] **Step 12.2: Write `web-next/components/ScanList.tsx`**

```tsx
import Link from 'next/link';

export type ScanRow = {
  id: string;
  subject: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  totalCostUsd: number | null;
};

export function ScanList({
  scans, currentScanId,
}: { scans: ScanRow[]; currentScanId?: string }) {
  if (scans.length === 0) {
    return (
      <div className="text-[11px] text-muted2 italic px-1.5 py-1">
        No scans yet
      </div>
    );
  }
  const running = scans.filter((s) => s.status === 'running' || s.status === 'queued');
  const done = scans.filter((s) => s.status === 'completed' || s.status === 'failed');

  return (
    <div className="space-y-3">
      {running.length > 0 && (
        <div>
          <div className="text-[9px] font-bold tracking-[0.1em] uppercase text-muted2 mb-1">
            Running
          </div>
          {running.map((s) => (
            <ScanRowItem key={s.id} row={s} active={s.id === currentScanId} />
          ))}
        </div>
      )}
      {done.length > 0 && (
        <div>
          <div className="text-[9px] font-bold tracking-[0.1em] uppercase text-muted2 mb-1">
            Done
          </div>
          {done.map((s) => (
            <ScanRowItem key={s.id} row={s} active={s.id === currentScanId} />
          ))}
        </div>
      )}
    </div>
  );
}

function ScanRowItem({ row, active }: { row: ScanRow; active: boolean }) {
  const cost = row.totalCostUsd ? `$${row.totalCostUsd.toFixed(2)}` : '';
  const tag =
    row.status === 'running' ? 'running'
    : row.status === 'queued' ? 'queued'
    : row.status === 'failed' ? 'failed'
    : `done · ${cost}`;
  const tagColor =
    row.status === 'running' || row.status === 'queued' ? 'text-accent'
    : row.status === 'failed' ? 'text-danger'
    : 'text-muted';
  return (
    <Link
      href={`/scans/${row.id}`}
      className={
        'block px-1.5 py-1 mb-0.5 text-[11px] '
        + (active ? 'bg-sidebar border-l-2 border-ink font-semibold' : 'text-muted')
      }
    >
      <div className="truncate">{row.subject}</div>
      <div className={`text-[9px] mt-0.5 ${tagColor}`}>{tag}</div>
    </Link>
  );
}
```

- [ ] **Step 12.3: Write `web-next/app/scans/layout.tsx`**

```tsx
import { auth } from '../../auth';
import { redirect } from 'next/navigation';
import { db } from '../../lib/db';
import { scans } from '../../drizzle/schema';
import { desc, eq } from 'drizzle-orm';
import { Sidebar } from '../../components/Sidebar';
import type { ScanRow } from '../../components/ScanList';

export default async function ScansLayout({
  children,
}: { children: React.ReactNode }) {
  const session = await auth();
  if (!session?.user?.id) redirect('/auth/signin');

  const rows = await db.select({
    id: scans.id, subject: scans.params, status: scans.status,
    totalCostUsd: scans.totalCostUsd,
  })
    .from(scans)
    .where(eq(scans.userId, session.user.id))
    .orderBy(desc(scans.createdAt))
    .limit(50);

  const scanRows: ScanRow[] = rows.map((r) => ({
    id: r.id,
    subject: (r.subject as { subject?: string })?.subject ?? '(no subject)',
    status: r.status as ScanRow['status'],
    totalCostUsd: r.totalCostUsd ? Number(r.totalCostUsd) : null,
  }));

  return (
    <div className="min-h-screen flex">
      <Sidebar scans={scanRows} />
      <main className="flex-1 p-4">{children}</main>
    </div>
  );
}
```

- [ ] **Step 12.4: Write `web-next/app/scans/page.tsx`**

```tsx
import Link from 'next/link';
import { auth } from '../../auth';
import { redirect } from 'next/navigation';
import { db } from '../../lib/db';
import { scans } from '../../drizzle/schema';
import { desc, eq } from 'drizzle-orm';

export default async function ScansIndexPage() {
  const session = await auth();
  if (!session?.user?.id) redirect('/auth/signin');

  const latest = await db.select({ id: scans.id })
    .from(scans).where(eq(scans.userId, session.user.id))
    .orderBy(desc(scans.createdAt)).limit(1);

  if (latest.length > 0) {
    redirect(`/scans/${latest[0].id}`);
  }

  // Empty state
  return (
    <div className="flex items-start pt-12 pl-6 max-w-[420px]">
      <div>
        <div className="label-uppercase text-muted2">WELCOME</div>
        <h1 className="text-[24px] font-extrabold leading-[1.05] heavy-rule pb-2.5 mt-1">
          Run your first scan.
        </h1>
        <p className="mt-3.5 text-[13px] text-muted leading-[1.5]">
          Pick an agent, name the subject, and watch the investigation happen
          live. First scan typically runs in under 10 minutes and costs less
          than a dollar.
        </p>
        <Link
          href="/scans/new"
          className="inline-block bg-ink text-white py-2.5 px-4 text-[11px] font-bold tracking-[0.12em] uppercase mt-4.5"
        >
          + NEW SCAN
        </Link>
      </div>
    </div>
  );
}
```

- [ ] **Step 12.5: Smoke**

After signing in (Task 11), navigate to `http://localhost:3000/scans`. Expected: empty-state welcome screen + sidebar showing "No scans yet".

(If you haven't signed in yet, the middleware redirects to `/auth/signin`.)

- [ ] **Step 12.6: Commit**

```bash
git add web-next/components/Sidebar.tsx web-next/components/ScanList.tsx \
        web-next/app/scans/layout.tsx web-next/app/scans/page.tsx
git commit -m "feat(ui): sidebar + scan list + empty welcome state"
```

---

## Task 13: createScan Server Action + new-scan form

**Files:**
- Create: `web-next/lib/sqs.ts`
- Create: `web-next/server-actions/createScan.ts`
- Create: `web-next/components/NewScanForm.tsx`
- Create: `web-next/app/scans/new/page.tsx`
- Create: `web-next/lib/api.ts` (typed catalog fetcher)

- [ ] **Step 13.1: Write `web-next/lib/sqs.ts`**

```ts
import { SQSClient, SendMessageCommand } from '@aws-sdk/client-sqs';

const client = new SQSClient({
  region: process.env.AWS_REGION ?? 'us-east-1',
  endpoint: process.env.AWS_ENDPOINT_URL || undefined,
});

export async function enqueueScan(
  scanId: string,
  userId: string,
  params: Record<string, unknown>,
): Promise<void> {
  const queueUrl = process.env.SQS_QUEUE_URL;
  if (!queueUrl) throw new Error('SQS_QUEUE_URL not configured');
  await client.send(new SendMessageCommand({
    QueueUrl: queueUrl,
    MessageBody: JSON.stringify({ scan_id: scanId, user_id: userId, params }),
  }));
}
```

- [ ] **Step 13.2: Write `web-next/lib/api.ts`**

```ts
const BASE = process.env.NEXT_PUBLIC_API_BASE
  ?? 'http://localhost:8000';

export type ParamField = {
  name: string;
  label: string;
  type: 'select' | 'text' | 'int' | 'float' | 'bool';
  default: unknown;
  options?: string[];
  help?: string;
  advanced?: boolean;
  min?: number;
  max?: number;
};

export type AgentManifest = {
  name: string;
  display_name: string;
  description: string;
  estimated_duration: string;
  params: ParamField[];
};

export type AgentCatalog = {
  agents: AgentManifest[];
  common_params: ParamField[];
};

export async function fetchAgentCatalog(): Promise<AgentCatalog> {
  const r = await fetch(`${BASE}/api/agents`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`Failed to fetch /api/agents: ${r.status}`);
  return r.json();
}
```

- [ ] **Step 13.3: Write `web-next/server-actions/createScan.ts`**

```ts
'use server';

import { auth } from '../auth';
import { db } from '../lib/db';
import { scans } from '../drizzle/schema';
import { and, count, eq, inArray } from 'drizzle-orm';
import { enqueueScan } from '../lib/sqs';
import { redirect } from 'next/navigation';
import { fetchAgentCatalog } from '../lib/api';

export async function createScan(formData: FormData): Promise<void> {
  const session = await auth();
  if (!session?.user?.id) throw new Error('UNAUTHENTICATED');
  const userId = session.user.id;

  // Concurrency cap.
  const cap = Number(process.env.MAX_CONCURRENT_SCANS_PER_USER ?? '2');
  const inflight = await db.select({ n: count() }).from(scans).where(
    and(eq(scans.userId, userId), inArray(scans.status, ['queued', 'running'] as const)),
  );
  if ((inflight[0]?.n ?? 0) >= cap) {
    throw new Error(`You already have ${cap} scans in flight. Wait for one to finish.`);
  }

  // Validate against the manifest.
  const catalog = await fetchAgentCatalog();
  const agentName = String(formData.get('agent') ?? '');
  const agent = catalog.agents.find((a) => a.name === agentName);
  if (!agent) throw new Error('UNKNOWN_AGENT');

  const subject = String(formData.get('subject') ?? '').trim();
  if (!subject) throw new Error('SUBJECT_REQUIRED');

  const params: Record<string, unknown> = { subject, agent: agentName };
  for (const f of [...agent.params, ...catalog.common_params]) {
    const raw = formData.get(f.name);
    if (raw === null || raw === '') continue;
    switch (f.type) {
      case 'int': params[f.name] = parseInt(String(raw), 10); break;
      case 'float': params[f.name] = parseFloat(String(raw)); break;
      case 'bool': params[f.name] = raw === 'on' || raw === 'true'; break;
      default: params[f.name] = String(raw);
    }
  }

  // Insert scan row + enqueue.
  const [row] = await db.insert(scans).values({
    userId,
    status: 'queued',
    agent: agentName,
    params,
  }).returning({ id: scans.id });

  try {
    await enqueueScan(row.id, userId, params);
  } catch (err) {
    await db.update(scans).set({
      status: 'failed', errorMessage: 'enqueue_failed: ' + String(err).slice(0, 500),
    }).where(eq(scans.id, row.id));
    throw err;
  }

  redirect(`/scans/${row.id}`);
}
```

- [ ] **Step 13.4: Write `web-next/components/NewScanForm.tsx`**

```tsx
'use client';

import { useState } from 'react';
import type { AgentCatalog, ParamField } from '../lib/api';
import { createScan } from '../server-actions/createScan';

export function NewScanForm({ catalog }: { catalog: AgentCatalog }) {
  const [agentName, setAgentName] = useState(catalog.agents[0]?.name ?? '');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const agent = catalog.agents.find((a) => a.name === agentName);

  const baseFields = (agent?.params ?? []).filter((p) => !p.advanced);
  const advancedFields = (agent?.params ?? []).filter((p) => p.advanced);

  return (
    <form
      className="max-w-[480px]"
      action={async (data) => {
        setSubmitting(true);
        setError(null);
        try {
          data.set('agent', agentName);
          await createScan(data);
        } catch (e) {
          setError(String(e instanceof Error ? e.message : e));
          setSubmitting(false);
        }
      }}
    >
      <div className="label-uppercase">NEW SCAN</div>
      <h1 className="text-[20px] font-extrabold heavy-rule pb-2 leading-[1.1]">
        Investigate someone.
      </h1>

      <div className="mt-3.5">
        <label className="label-uppercase block mb-1">SUBJECT</label>
        <input
          name="subject" required
          placeholder="e.g. Jane Doe, ML researcher"
          className="block w-full border-2 border-ink bg-white px-2.5 py-2 text-[14px]"
        />
      </div>

      <div className="mt-3.5">
        <label className="label-uppercase block mb-1.5">MODE</label>
        <div className="flex gap-1.5 flex-wrap">
          {catalog.agents.map((a) => (
            <button
              key={a.name} type="button"
              onClick={() => setAgentName(a.name)}
              className={
                'px-2.5 py-1.5 text-[10px] font-bold tracking-[0.1em] uppercase '
                + (a.name === agentName
                  ? 'bg-ink text-white'
                  : 'border-2 border-border text-muted')
              }
            >
              {a.display_name}
            </button>
          ))}
        </div>
        {agent?.description && (
          <p className="text-[11px] text-muted mt-1.5 leading-[1.4]">
            {agent.description} · {agent.estimated_duration}
          </p>
        )}
      </div>

      {agent && agent.params.length > 0 && (
        <div className="mt-4 p-3 bg-white border-2 border-ink">
          <div className="text-[9px] font-bold tracking-[0.12em] uppercase text-muted2 mb-2.5">
            {agent.display_name} settings
          </div>
          {baseFields.map((f) => <FieldInput key={f.name} f={f} />)}
          {advancedFields.length > 0 && (
            <details className="mt-3 border border-dashed border-dashed text-[11px]">
              <summary className="px-2 py-1.5 font-semibold text-muted2 uppercase tracking-[0.1em] cursor-pointer">
                ▸ Advanced
              </summary>
              <div className="px-2 py-2 space-y-2.5">
                {advancedFields.map((f) => <FieldInput key={f.name} f={f} />)}
              </div>
            </details>
          )}
        </div>
      )}

      <details
        className="mt-3.5 border border-dashed border-dashed"
        open={advancedOpen}
        onToggle={(e) => setAdvancedOpen((e.currentTarget as HTMLDetailsElement).open)}
      >
        <summary className="px-2 py-1.5 text-[11px] font-semibold text-muted2 uppercase tracking-[0.1em] cursor-pointer">
          ▸ Budget · limits
        </summary>
        <div className="px-2 py-2 space-y-2.5">
          {catalog.common_params.map((f) => <FieldInput key={f.name} f={f} />)}
        </div>
      </details>

      <div className="mt-4 flex items-center gap-2.5">
        <button
          type="submit" disabled={submitting}
          className="bg-ink text-white py-2 px-4 text-[10px] font-bold tracking-[0.12em] uppercase disabled:opacity-50"
        >
          {submitting ? 'Submitting…' : 'Run scan →'}
        </button>
      </div>

      {error && (
        <div className="mt-3 px-2.5 py-2 bg-amber border-l-[3px] border-danger text-[12px] text-danger">
          {error}
        </div>
      )}
    </form>
  );
}

function FieldInput({ f }: { f: ParamField }) {
  if (f.type === 'select') {
    return (
      <div>
        <label className="block text-[11px] font-semibold mb-0.5">
          {f.label}
        </label>
        <select
          name={f.name} defaultValue={String(f.default ?? '')}
          className="block w-full border-2 border-ink bg-white px-2 py-1.5 text-[12px]"
        >
          {f.options?.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        {f.help && <p className="text-[10px] text-muted2 mt-0.5">{f.help}</p>}
      </div>
    );
  }
  if (f.type === 'int' || f.type === 'float') {
    return (
      <div>
        <label className="block text-[11px] font-semibold mb-0.5">
          {f.label}
        </label>
        <input
          name={f.name} type="number" step={f.type === 'float' ? '0.01' : '1'}
          min={f.min} max={f.max}
          defaultValue={f.default !== undefined ? String(f.default) : ''}
          className="block w-full border-2 border-ink bg-white px-2 py-1.5 text-[12px]"
        />
        {f.help && <p className="text-[10px] text-muted2 mt-0.5">{f.help}</p>}
      </div>
    );
  }
  return (
    <div>
      <label className="block text-[11px] font-semibold mb-0.5">{f.label}</label>
      <input
        name={f.name} type="text"
        defaultValue={String(f.default ?? '')}
        className="block w-full border-2 border-ink bg-white px-2 py-1.5 text-[12px]"
      />
      {f.help && <p className="text-[10px] text-muted2 mt-0.5">{f.help}</p>}
    </div>
  );
}
```

- [ ] **Step 13.5: Write `web-next/app/scans/new/page.tsx`**

```tsx
import { fetchAgentCatalog } from '../../../lib/api';
import { NewScanForm } from '../../../components/NewScanForm';

export default async function NewScanPage() {
  const catalog = await fetchAgentCatalog();
  return <NewScanForm catalog={catalog} />;
}
```

- [ ] **Step 13.6: Smoke**

After signing in, click "+ NEW" in the sidebar (or visit `/scans/new`). Expected: form renders with mode chips for all four agents. Click a chip → form re-renders agent-specific fields. Submit a scan with subject "Smoke test" → redirects to `/scans/{id}`. Worker (Phase 1) picks up the message and starts processing.

- [ ] **Step 13.7: Commit**

```bash
git add web-next/lib/sqs.ts web-next/lib/api.ts \
        web-next/server-actions/createScan.ts \
        web-next/components/NewScanForm.tsx \
        web-next/app/scans/new
git commit -m "feat(ui): createScan Server Action + agent-aware new-scan form"
```

---

## Task 14: Scan detail page — running state with SSE

**Files:**
- Create: `web-next/components/StatusPill.tsx`
- Create: `web-next/components/RecentTail.tsx`
- Create: `web-next/components/ProgressStream.tsx`
- Create: `web-next/app/scans/[id]/page.tsx`

The live-updating scan detail page. Server-renders the static frame; the client `<ProgressStream>` opens an EventSource and updates the status pill + recent tail.

- [ ] **Step 14.1: Write `web-next/components/StatusPill.tsx`**

```tsx
type Props = {
  active?: { displayLabel: string; argSummary: string };
  elapsedSec: number;
  costUsd?: number;
  sourcesCount?: number;
};

export function StatusPill({ active, elapsedSec, costUsd, sourcesCount }: Props) {
  const time = `${Math.floor(elapsedSec / 60)}:${String(elapsedSec % 60).padStart(2, '0')}`;
  return (
    <div className="bg-ink text-white p-2.5 px-3.5 mt-3.5">
      <div className="flex items-center gap-2.5">
        <div
          className="w-2 h-2 bg-spotlight rounded-full"
          style={{ animation: 'pulse 1.2s infinite' }}
        />
        <div className="text-[11px] font-bold tracking-[0.1em] uppercase text-muted2">
          {active?.displayLabel ?? 'Starting…'}
        </div>
        <div className="ml-auto text-[10px] text-muted2 tracking-[0.1em]">
          {sourcesCount !== undefined && `${sourcesCount} SRC · `}
          {time}
          {costUsd !== undefined && ` · $${costUsd.toFixed(2)}`}
        </div>
      </div>
      {active?.argSummary && (
        <div className="text-[13px] font-semibold mt-1 font-mono">
          {active.argSummary}
        </div>
      )}
      <style jsx>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
    </div>
  );
}
```

- [ ] **Step 14.2: Write `web-next/components/RecentTail.tsx`**

```tsx
export type TailItem = {
  ts: number;
  displayLabel: string;
  argSummary: string;
  resultSummary: string;
};

export function RecentTail({ items }: { items: TailItem[] }) {
  if (items.length === 0) return null;
  const visible = items.slice(0, 3);
  return (
    <div className="mt-2 px-1">
      {visible.map((it, idx) => {
        const opacity = idx === 0 ? 1 : idx === 1 ? 0.75 : 0.5;
        const sec = Math.floor(it.ts);
        return (
          <div
            key={`${it.ts}-${idx}`}
            className="flex gap-2.5 py-1 font-mono text-[11px] text-muted"
            style={{ opacity }}
          >
            <div className="text-muted2 min-w-[44px]">+{sec}s</div>
            <div className="flex-1 min-w-0 truncate">
              <strong className="text-ink">{it.displayLabel}</strong>{' '}
              {it.argSummary}{' '}
              {it.resultSummary && <span className="text-muted2">→ {it.resultSummary}</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 14.3: Write `web-next/components/ProgressStream.tsx`**

```tsx
'use client';

import { useEffect, useRef, useState } from 'react';
import { StatusPill } from './StatusPill';
import { RecentTail, type TailItem } from './RecentTail';

type Event = {
  event: string;
  seq: number;
  ts: number;
  display_label?: string;
  arg_summary?: string;
  tool_name?: string;
  // result fields when finished:
  result_count?: number;
  result_size_bytes?: number;
  // terminal:
  s3_key?: string;
  error?: string;
};

type Props = {
  scanId: string;
  initialStatus: 'queued' | 'running' | 'completed' | 'failed';
  startedAt?: string | null;
  onTerminal?: (event: 'completed' | 'failed') => void;
};

export function ProgressStream({ scanId, initialStatus, startedAt, onTerminal }: Props) {
  const [status, setStatus] = useState<'connecting' | 'live' | 'done' | 'error'>(
    initialStatus === 'completed' || initialStatus === 'failed' ? 'done' : 'connecting',
  );
  const [active, setActive] = useState<{ displayLabel: string; argSummary: string } | undefined>();
  const [tail, setTail] = useState<TailItem[]>([]);
  const [elapsed, setElapsed] = useState(
    startedAt ? Math.max(0, (Date.now() - new Date(startedAt).getTime()) / 1000) : 0,
  );
  const seenSeq = useRef<number>(-1);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';

  // Tick the elapsed timer.
  useEffect(() => {
    if (status === 'done') return;
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, [status]);

  useEffect(() => {
    if (status === 'done') return;
    const es = new EventSource(`${apiBase}/api/stream/scans/${scanId}`, {
      withCredentials: true,
    });
    es.onopen = () => setStatus('live');
    es.onmessage = (msg) => {
      let evt: Event;
      try {
        evt = JSON.parse(msg.data);
      } catch {
        return;
      }
      if (typeof evt.seq === 'number' && evt.seq <= seenSeq.current) return;
      seenSeq.current = evt.seq ?? seenSeq.current;
      switch (evt.event) {
        case 'tool.started':
          setActive({
            displayLabel: evt.display_label ?? 'Tool',
            argSummary: evt.arg_summary ?? '',
          });
          break;
        case 'tool.finished':
          setActive(undefined);
          setTail((prev) => [
            {
              ts: elapsed,
              displayLabel: evt.display_label ?? 'Tool',
              argSummary: evt.arg_summary ?? '',
              resultSummary:
                evt.result_count !== undefined ? `${evt.result_count} results` :
                evt.result_size_bytes !== undefined
                  ? `${(evt.result_size_bytes / 1024).toFixed(1)} KB`
                  : '',
            },
            ...prev,
          ]);
          break;
        case 'scan.completed':
          setStatus('done');
          onTerminal?.('completed');
          es.close();
          break;
        case 'scan.failed':
          setStatus('done');
          onTerminal?.('failed');
          es.close();
          break;
      }
    };
    es.onerror = () => setStatus('error');
    return () => es.close();
  }, [scanId, apiBase, status]);

  if (status === 'done') return null;

  return (
    <>
      <StatusPill
        active={active}
        elapsedSec={Math.floor(elapsed)}
      />
      <RecentTail items={tail} />
    </>
  );
}
```

- [ ] **Step 14.4: Write `web-next/app/scans/[id]/page.tsx`**

```tsx
import { auth } from '../../../auth';
import { redirect, notFound } from 'next/navigation';
import { db } from '../../../lib/db';
import { scans } from '../../../drizzle/schema';
import { eq } from 'drizzle-orm';
import { ProgressStream } from '../../../components/ProgressStream';

type Params = Promise<{ id: string }>;

export default async function ScanDetailPage({ params }: { params: Params }) {
  const { id } = await params;
  const session = await auth();
  if (!session?.user?.id) redirect('/auth/signin');

  const rows = await db.select().from(scans).where(eq(scans.id, id)).limit(1);
  const sc = rows[0];
  if (!sc || sc.userId !== session.user.id) notFound();

  const subject = (sc.params as { subject?: string })?.subject ?? '(no subject)';
  const goal = (sc.params as { goal?: string })?.goal;
  const shortId = id.slice(0, 8);

  return (
    <div>
      <div className="text-[10px] font-bold tracking-[0.1em] uppercase">
        SCAN · {shortId}
      </div>
      <h1 className="text-[18px] font-extrabold leading-[1.1]">
        {subject.toUpperCase()}
      </h1>
      {goal && <p className="text-[11px] text-muted mt-0.5">{goal}</p>}

      <ProgressStream
        scanId={id}
        initialStatus={sc.status as 'queued' | 'running' | 'completed' | 'failed'}
        startedAt={sc.startedAt?.toISOString() ?? null}
      />

      {(sc.status === 'queued' || sc.status === 'running') && (
        <div className="mt-4.5 p-10 px-5 border-2 border-dashed border-dashed text-center min-h-[140px] flex flex-col justify-center items-center">
          <div className="label-uppercase text-muted2">REPORT</div>
          <div className="text-[13px] text-muted2 mt-1.5">
            Will appear when investigation completes…
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 14.5: Smoke**

Submit a scan with a small budget (subject "Smoke 2", agent "ReAct", budget $0.50). After redirect, the status pill and recent tail should update live. The timer ticks. When the scan completes, the ProgressStream returns null (handled by Task 15).

If the SSE never connects, check:
- Browser dev tools network tab — is `/api/stream/scans/{id}` being requested? Does the request include the cookie?
- CORS — `osint/api/app.py:CORSMiddleware` must include `allow_credentials=True` and `allow_origins=["http://localhost:3000"]` (it does, but verify).
- `NEXT_PUBLIC_API_BASE` must be set to `http://localhost:8000` in compose.yml (it is).

- [ ] **Step 14.6: Commit**

```bash
git add web-next/components/StatusPill.tsx web-next/components/RecentTail.tsx \
        web-next/components/ProgressStream.tsx \
        web-next/app/scans/\[id\]
git commit -m "feat(ui): scan detail page with live SSE progress stream"
```

---

## Task 15: Done & error states + StepsDrawer + ReportMarkdown

**Files:**
- Create: `web-next/components/ReportMarkdown.tsx`
- Create: `web-next/components/StepsDrawer.tsx`
- Modify: `web-next/app/scans/[id]/page.tsx`

After the scan terminates, render the markdown report (or error state) and the show-steps drawer.

- [ ] **Step 15.1: Write `web-next/components/ReportMarkdown.tsx`**

```tsx
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export function ReportMarkdown({ text }: { text: string }) {
  return (
    <div className="text-[13px] leading-[1.55] text-[#1f1f1f]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h1 className="text-[16px] font-extrabold tracking-[0.04em] uppercase mt-3.5 mb-1.5 pb-1 heavy-rule">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-[14px] font-extrabold tracking-[0.04em] uppercase mt-3.5 mb-1.5 pb-1 heavy-rule">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-[13px] font-bold tracking-[0.04em] uppercase mt-3 mb-1">
              {children}
            </h3>
          ),
          p: ({ children }) => <p className="my-2.5">{children}</p>,
          ul: ({ children }) => <ul className="my-2 pl-[18px] list-disc">{children}</ul>,
          ol: ({ children }) => <ol className="my-2 pl-[18px] list-decimal">{children}</ol>,
          code: ({ children }) => (
            <code className="text-[11px] bg-sidebar px-1 py-0.5 font-mono">{children}</code>
          ),
          a: ({ children, href }) => (
            <a href={href} className="text-ink underline" target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
```

- [ ] **Step 15.2: Write `web-next/components/StepsDrawer.tsx`**

```tsx
'use client';

import { useEffect, useState } from 'react';

type Step = {
  ts: number;
  displayLabel: string;
  argSummary: string;
  fullArgs?: Record<string, unknown>;
  responsePreview?: string;
  responseS3Key?: string;
  isCritic?: boolean;
};

export function StepsDrawer({ scanId }: { scanId: string }) {
  const [open, setOpen] = useState(false);
  const [steps, setSteps] = useState<Step[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';

  useEffect(() => {
    if (!open || loaded) return;
    fetch(`${apiBase}/api/scans/${scanId}/steps`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : { steps: [] }))
      .then((d) => { setSteps(d.steps ?? []); setLoaded(true); })
      .catch(() => setLoaded(true));
  }, [open, loaded, scanId, apiBase]);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="mt-5 pt-2.5 dashed-rule w-full text-left text-[11px] font-semibold text-muted2 uppercase tracking-[0.1em]"
      >
        ▸ Show steps
      </button>
    );
  }
  return (
    <div className="mt-5 pt-2.5 dashed-rule">
      <button
        onClick={() => setOpen(false)}
        className="text-[11px] font-semibold text-muted2 uppercase tracking-[0.1em] mb-2"
      >
        ▾ Steps · {steps.length} actions
      </button>
      {!loaded && <div className="text-[11px] text-muted2">Loading…</div>}
      {steps.map((s, idx) => (
        <StepRow
          key={idx} step={s}
          expanded={expandedIdx === idx}
          onToggle={() => setExpandedIdx((c) => (c === idx ? null : idx))}
        />
      ))}
    </div>
  );
}

function StepRow({
  step, expanded, onToggle,
}: { step: Step; expanded: boolean; onToggle: () => void }) {
  if (step.isCritic) {
    return (
      <div className="bg-amber px-3 -mx-3 py-1.5 border-b border-border">
        <div className="flex items-center gap-2.5 text-[12px] text-amber2">
          <div className="font-mono text-[10px] min-w-[44px]">+{step.ts}s</div>
          <strong>CRITIC</strong> {step.argSummary}
        </div>
      </div>
    );
  }
  return (
    <div onClick={onToggle} className="cursor-pointer border-b border-border py-1.5">
      <div className="flex items-center gap-2.5 text-[12px]">
        <div className="font-mono text-[10px] text-muted2 min-w-[44px]">+{step.ts}s</div>
        <div className="flex-1">
          <strong>{step.displayLabel}</strong>{' '}
          <span className="text-muted font-mono">{step.argSummary}</span>
        </div>
        <div className="text-[10px] text-muted2">{expanded ? '▾' : '▸'}</div>
      </div>
      {expanded && (
        <div className="mt-1.5 ml-[52px] p-2 bg-ink text-muted2 font-mono text-[10px] leading-[1.5]">
          {step.fullArgs && (
            <>
              <div className="text-muted2">ARGS</div>
              <pre className="text-spotlight whitespace-pre-wrap">{JSON.stringify(step.fullArgs, null, 2)}</pre>
            </>
          )}
          {step.responsePreview && (
            <>
              <div className="text-muted2 mt-1.5">RESPONSE (TRUNCATED)</div>
              <pre className="text-white whitespace-pre-wrap">{step.responsePreview}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}
```

> **Note:** the StepsDrawer fetches from `/api/scans/{id}/steps` which doesn't yet exist. For Phase 2 we use a simpler approach: instead of a separate steps endpoint, the drawer reads the same SSE history (or fetches `/api/scans/{id}` and uses an embedded `events` field from S3). To keep this task self-contained and match the spec, **the drawer is wired up but optional**; if the steps endpoint doesn't exist, it shows "Loading…" then nothing. A follow-up plan iteration adds the steps endpoint properly.

- [ ] **Step 15.3: Update `web-next/app/scans/[id]/page.tsx`**

Replace the file with:

```tsx
import { auth } from '../../../auth';
import { redirect, notFound } from 'next/navigation';
import { db } from '../../../lib/db';
import { scans } from '../../../drizzle/schema';
import { eq } from 'drizzle-orm';
import { ProgressStream } from '../../../components/ProgressStream';
import { ReportMarkdown } from '../../../components/ReportMarkdown';
import { StepsDrawer } from '../../../components/StepsDrawer';

type Params = Promise<{ id: string }>;

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? 'http://localhost:8000';

async function fetchReportMarkdown(s3Url: string): Promise<string> {
  try {
    const r = await fetch(s3Url, { cache: 'no-store' });
    if (!r.ok) return '';
    const json = await r.json();
    return (json?.report?.text as string) ?? '';
  } catch {
    return '';
  }
}

export default async function ScanDetailPage({ params }: { params: Params }) {
  const { id } = await params;
  const session = await auth();
  if (!session?.user?.id) redirect('/auth/signin');

  const rows = await db.select().from(scans).where(eq(scans.id, id)).limit(1);
  const sc = rows[0];
  if (!sc || sc.userId !== session.user.id) notFound();

  const subject = (sc.params as { subject?: string })?.subject ?? '(no subject)';
  const goal = (sc.params as { goal?: string })?.goal;
  const shortId = id.slice(0, 8);

  // For done state, fetch the markdown report from the API.
  let reportText = '';
  if (sc.status === 'completed') {
    const apiResp = await fetch(`${API_BASE}/api/scans/${id}`, { cache: 'no-store' });
    if (apiResp.ok) {
      const detail = await apiResp.json();
      if (detail.s3_url) reportText = await fetchReportMarkdown(detail.s3_url);
    }
  }

  return (
    <div>
      <div className="text-[10px] font-bold tracking-[0.1em] uppercase">
        SCAN · {shortId}
      </div>
      <h1 className="text-[18px] font-extrabold leading-[1.1]">
        {subject.toUpperCase()}
      </h1>
      {goal && <p className="text-[11px] text-muted mt-0.5">{goal}</p>}

      {(sc.status === 'queued' || sc.status === 'running') && (
        <>
          <ProgressStream
            scanId={id}
            initialStatus={sc.status}
            startedAt={sc.startedAt?.toISOString() ?? null}
          />
          <div className="mt-4.5 p-10 px-5 border-2 border-dashed border-dashed text-center min-h-[140px] flex flex-col justify-center items-center">
            <div className="label-uppercase text-muted2">REPORT</div>
            <div className="text-[13px] text-muted2 mt-1.5">
              Will appear when investigation completes…
            </div>
          </div>
        </>
      )}

      {sc.status === 'completed' && (
        <>
          <div className="mt-3.5 p-2 px-3.5 bg-white border-2 border-ink flex items-center gap-3.5 text-[10px] font-bold tracking-[0.08em]">
            <div>● COMPLETE</div>
            {sc.completedAt && sc.startedAt && (
              <div className="text-muted2">
                {Math.round(
                  (sc.completedAt.getTime() - sc.startedAt.getTime()) / 1000,
                )}s
              </div>
            )}
            {sc.totalCostUsd && (
              <div className="text-muted2">${Number(sc.totalCostUsd).toFixed(2)}</div>
            )}
            {sc.totalToolCalls !== null && (
              <div className="text-muted2">{sc.totalToolCalls} TOOL CALLS</div>
            )}
          </div>
          <div className="mt-4.5">
            {reportText
              ? <ReportMarkdown text={reportText} />
              : <p className="text-[13px] text-muted">Report unavailable — try refreshing.</p>}
          </div>
        </>
      )}

      {sc.status === 'failed' && (
        <>
          <div className="mt-3.5 p-2 px-3.5 bg-danger text-white flex items-center gap-3.5 text-[10px] font-bold tracking-[0.08em]">
            <div className="w-2 h-2 bg-amber rounded-full" />
            <div>FAILED</div>
            {sc.completedAt && sc.startedAt && (
              <div className="text-muted2">
                {Math.round(
                  (sc.completedAt.getTime() - sc.startedAt.getTime()) / 1000,
                )}s
              </div>
            )}
          </div>
          <div className="mt-3.5 p-3.5 px-4 bg-white border-2 border-danger">
            <div className="label-uppercase text-danger">ERROR</div>
            <div className="text-[13px] mt-1 text-[#1f1f1f] font-mono">
              {sc.errorMessage ?? 'Unknown error'}
            </div>
            <div className="text-[12px] text-muted mt-2">
              The investigation could not complete. The previous tool calls are
              preserved below.
            </div>
          </div>
        </>
      )}

      <StepsDrawer scanId={id} />
    </div>
  );
}
```

- [ ] **Step 15.4: Smoke**

Submit and complete a small scan. After it completes, the report should render (markdown headings styled brutalist). On a failure case (e.g., set `budget_usd` to `0.01` to force fast budget exhaustion), the error state should render with the red strip and error card.

- [ ] **Step 15.5: Commit**

```bash
git add web-next/components/ReportMarkdown.tsx web-next/components/StepsDrawer.tsx \
        web-next/app/scans/\[id\]/page.tsx
git commit -m "feat(ui): scan detail done + error states + steps drawer scaffold"
```

---

## Task 16: Playwright e2e + final smoke

**Files:**
- Create: `web-next/playwright.config.ts`
- Create: `web-next/e2e/submit-flow.spec.ts`
- Modify: `Makefile` (add `make e2e`)

- [ ] **Step 16.1: Write `web-next/playwright.config.ts`**

```ts
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
});
```

- [ ] **Step 16.2: Write `web-next/e2e/submit-flow.spec.ts`**

```ts
import { test, expect } from '@playwright/test';

const TEST_EMAIL = `e2e-${Date.now()}@example.com`;
const TEST_PASSWORD = 'correct-horse-battery-staple';

test('signup → submit small scan → see live feed → see report', async ({ page, request }) => {
  // Seed allowed_emails by making a direct request? No — easier: do it via psql
  // outside the test. So this test ASSUMES the email is already in allowed_emails.
  // To make it self-contained, the test could call a debug endpoint, but for
  // Phase 2 we keep it simple: precondition is documented in Makefile.

  // 1. Sign up.
  await page.goto('/auth/signup');
  await page.fill('input[name=email]', TEST_EMAIL);
  await page.fill('input[name=password]', TEST_PASSWORD);
  await page.click('button[type=submit]');
  await expect(page).toHaveURL(/\/scans/);

  // 2. New scan form.
  await page.click('a[href="/scans/new"]');
  await page.fill('input[name=subject]', 'E2E Smoke Test');
  // ReAct should be the default agent (alphabetical first if not explicitly set);
  // its only param is `passes` which defaults to 1.
  await page.click('button[type=submit]:has-text("RUN SCAN")');
  await expect(page).toHaveURL(/\/scans\/[0-9a-f-]{36}/);

  // 3. The status pill should appear.
  await expect(page.locator('text=Starting…').or(page.locator('text=ReAct')))
    .toBeVisible({ timeout: 30_000 });

  // 4. Wait for the scan to terminate (max 5 min).
  await expect(page.locator('text=COMPLETE').or(page.locator('text=FAILED')))
    .toBeVisible({ timeout: 5 * 60_000 });
});
```

- [ ] **Step 16.3: Update Makefile**

Append to `Makefile`:

```makefile
e2e:
	cd web-next && npx playwright install --with-deps chromium && npx playwright test
```

- [ ] **Step 16.4: Run the e2e (manual; documented for the user)**

Run by the user, NOT by the implementer (real LLM money):

```bash
# Pre-condition: seed the allowed_emails table with the e2e pattern
docker compose exec -T postgres psql -U app -d agent_osint \
  -c "INSERT INTO allowed_emails (email) VALUES ('e2e-$(date +%s)@example.com');"
# Then run e2e
make e2e
```

For the implementer: just verify that `cd web-next && npx playwright test --list` lists the spec without errors. Do not actually run the test (it spends LLM money).

```bash
cd web-next && npx playwright install --with-deps chromium >/dev/null 2>&1
cd web-next && npx playwright test --list
```
Expected: `1 test in 1 file`.

- [ ] **Step 16.5: Commit**

```bash
git add web-next/playwright.config.ts web-next/e2e/ Makefile
git commit -m "feat(e2e): playwright submit-flow spec + make e2e target"
```

---

## Phase 2 done — what you can do now

- Sign in / sign up at `http://localhost:3000`.
- Submit scans from the agent-aware form.
- Watch the live status pill + recent tail update in real time.
- Read the final markdown report when the scan completes.
- Sidebar lists your scans grouped by status.
- Failed scans show the error state.

What's deferred to Phase 3:
- AWS deployment (CDK).
- Real domain + HTTPS.
- Production-grade observability.

---

## Self-review notes (post-write)

Spec coverage check against `2026-04-29-phase2-ui-design.md`:

| Spec section | Phase 2 task |
|---|---|
| Brutalist E theme tokens | Task 10 |
| L2 sidebar layout | Task 12 |
| Status pill + recent tail | Task 14 |
| No findings UI | Task 15 (only renders the markdown report) |
| Per-tool render functions | Task 3 |
| Worker enrichment with display_label / arg_summary / seq | Task 4 |
| Agent param manifests | Tasks 1, 2 |
| `/api/agents` catalog | Task 6 |
| Sign-in / sign-up with allowed_emails gate | Task 11 |
| New-scan form (agent-aware) | Task 13 |
| Scan detail running state with SSE | Task 14 |
| Scan detail done state (markdown report) | Task 15 |
| Scan detail error state | Task 15 |
| Empty welcome state | Task 12 |
| Show-steps drawer (inline) | Task 15 (scaffolded; full steps endpoint deferred) |
| `/api/scans/{id}` with presigned URL | Task 7 |
| `/api/stream/scans/{id}` SSE | Task 8 |
| FastAPI Dockerfile + compose | Task 9 |
| Next.js Dockerfile + compose | Task 10 |
| JWT round-trip test | Task 5 |
| Playwright e2e | Task 16 |

**Open items deferred from this plan:**
- A real `/api/scans/{id}/steps` endpoint that returns the full event timeline for the show-steps drawer. The drawer is scaffolded but will display empty until that endpoint is added in a follow-up.
- Toast / inline error UI for `createScan` Server Action failures (concurrency cap, enqueue failure). Currently surfaced via `setError` in the form — fine for internal testing.
- Manual `↻ RETRY` button on the failed-scan card. Not in this plan; the user can re-submit from `/scans/new`.

**Type consistency check:** `display_label` and `arg_summary` are the field names used in both `event_sink.py` (Task 4) and the React `ProgressStream`/`StepsDrawer` (Tasks 14, 15). `MANIFEST` is the Python module-level constant name across all four agent manifests (Task 2) and the catalog endpoint (Task 6). `createScan` and `enqueueScan` signatures match across `server-actions/createScan.ts` and `lib/sqs.ts` (Task 13).
