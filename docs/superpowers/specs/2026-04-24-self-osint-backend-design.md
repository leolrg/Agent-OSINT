# Self-OSINT Backend — v1 Design

**Status:** Draft
**Date:** 2026-04-24
**Scope:** v1 — agent logic only, library-shaped, no HTTP

## 1. Overview

A Python package that, given a person's self-supplied identifiers (name + any subset of quantifiers — emails, phones, usernames, LinkedIn URL, Instagram URL, X handle, school, employer, city, DOB, etc.), runs an LLM agent that drives a set of OSINT tools and returns a comprehensive evidence-backed report of what is discoverable about that person on the public internet.

The product category is **self-OSINT**: the caller is the subject. Access gating (e.g. photo-proof verification that the caller *is* the subject) is explicitly out of scope for this backend and is handled by the calling layer.

## 2. Goals

- **Recall.** Miss as little publicly-discoverable information as possible across the selected sources.
- **Evidence preservation.** Every finding is backed by a raw tool response that is stored verbatim. Nothing discoverable is silently dropped.
- **Toggleable sources.** The caller can enable/disable individual tools per scan to respect budget, paid-API availability, or policy.
- **Forward-compatibility with a web app.** v1 is a library, but its rate-limiting and concurrency primitives are chosen so that wrapping it in HTTP + multi-tenant later does not require redesign.

## 3. Non-goals (v1)

- HTTP / FastAPI surface. Added later.
- Multi-tenant user management, per-user quotas, authentication. Added later.
- Entity-resolution pass separate from the LLM agent. The agent's in-loop reasoning is the only resolution step in v1.
- Formal typed entity+evidence graph. v1 output is a report + a verbatim tool-call log.
- Face search, breach-password databases, data-broker people-search, dark-web crawling. Not in v1 tool set.
- Cross-scan caching.

## 4. Scope of v1

**In scope:**
- One Python package, async, invoked via `await scan(...)`.
- A single LLM vendor (xAI Grok 4.20).
- Six tools (see §9).
- JSON-file-per-scan persistence under a configurable directory.
- Process-wide per-vendor and per-tool concurrency limits (asyncio semaphores).
- Budget / tool-call-count / wall-clock caps enforced per scan.

**Not in scope:** everything listed in §3.

## 5. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      scan() entrypoint                        │
│                    (async Python function)                    │
└───────────────────────────┬──────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                       Agent loop                              │
│                                                               │
│   ┌─────────┐     LLM.call(messages, tools)                   │
│   │  Grok   │ ◄──────────── tool_use blocks ◄──────── turn N  │
│   │ 4.20    │                                                 │
│   └─────────┘ ──── asyncio.gather(invoke_tool, ...) ──►       │
│                         │                                     │
│                         ▼                                     │
│              tool results ───► turn N+1 messages              │
│                                                               │
│   Stop when: no tool_uses / budget / max_calls / wall-clock   │
└───────────────────────────┬──────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                      Tool registry                            │
│                                                               │
│   tavily_search        (free-tier)                            │
│   tavily_extract       (free-tier)                            │
│   maigret              (free, local library)                  │
│   apify_instagram      (paid, Apify actor)                    │
│   apify_linkedin       (paid, Apify actor)                    │
│   apify_twitter        (paid, Apify X/Twitter scraper actor)  │
│                                                               │
│   Each: async run(args) → dict. Registered, schema-described, │
│   cost-estimated, guarded by per-vendor semaphore.            │
└───────────────────────────┬──────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                     JSON-per-scan storage                     │
│   {scans_dir}/{scan_id}.json contains:                        │
│   - subject input                                             │
│   - final report                                              │
│   - every tool call with verbatim raw response                │
│   - costs, timings                                            │
└──────────────────────────────────────────────────────────────┘
```

## 6. Component detail

### 6.1 Subject input

A single free-form string. Whatever the caller wants to tell the agent about the subject — name, known emails, handles, phone numbers, school, employer, city, past addresses, relationship to known people, "I think I had a Twitter around 2011 but forget the handle" — all in one natural-language blob. Empty or whitespace-only input is rejected.

```python
async def scan(subject: str, config: ScanConfig = ..., ...) -> ScanResult
```

Rationale: modern LLMs parse identifiers out of natural language reliably, and forcing the caller to map their knowledge into ~15 optional typed fields is friction for no gain. The agent's system prompt instructs Grok to (a) extract identifiers from the subject string into an internal working set, (b) use them to drive tool calls, (c) surface the extracted identifiers in the final report so the caller can confirm nothing was misread.

Callers who already have structured data format it into the string themselves (e.g. `f"Name: {name}\nEmail: {email}\nLinkedIn: {url}"`) — the LLM reads both forms equally well. If a later version wants a structured-input convenience wrapper, it's a thin helper that formats fields into a string and calls `scan()`.

### 6.2 Agent loop

Pseudocode (real implementation ~50 lines):

```python
async def scan(subject: str, config, llm=None, scans_dir=...):
    if not subject or not subject.strip():
        raise ValueError("subject must be a non-empty description")
    llm = llm or GrokLLM(model="grok-4.20")
    tools = [REGISTRY[name] for name in config.enabled_tools]
    state = ScanState(subject=subject, limits=config.limits)
    messages = [build_system_prompt(subject, tools)]

    while not state.should_stop():
        response = await llm.call(messages, tools=tools)
        if not response.tool_uses:
            state.record_final_report(response.text)
            break

        results = await asyncio.gather(*[
            invoke_tool(tu, state) for tu in response.tool_uses
        ])
        messages.extend([response.assistant_message,
                         build_tool_results_message(results)])
        state.record_turn(response, results)

    if not state.has_final_report():
        state.record_final_report(await llm.synthesize(state))

    await storage.write(scans_dir, state)
    return ScanResult.from_state(state)
```

**Parallel tool dispatch** is the main reason for async: when Grok emits multiple independent `tool_use` blocks in a single turn (common), `asyncio.gather` fans them out concurrently, bounded by per-vendor semaphores.

**`invoke_tool`** wraps each call with: tool-schema validation → budget check (skip if over budget) → per-vendor semaphore acquire → call → append raw response to `state.tool_calls` verbatim → release → return structured result. On tool error, the error text is returned to the LLM (agent decides whether to retry or move on); the error is also logged in `state.tool_calls`.

**Stop conditions** (any of): the LLM's turn produced no `tool_use` blocks, the budget is exhausted, `max_tool_calls` is exhausted, the wall-clock cap is hit.

### 6.3 Tool registry and contract

```python
class Tool(Protocol):
    name: str
    description: str
    input_schema: dict              # JSON Schema for the LLM
    tier: Literal["free", "paid"]
    est_cost_usd_per_call: float
    vendor: str                     # for per-vendor semaphore grouping
    async def run(self, **kwargs) -> dict: ...
```

`REGISTRY: dict[str, Tool]` is a module-level dict populated by each tool module on import.

Adding a new tool = one file under `osint/tools/`, one class, one registry entry. No orchestrator changes.

### 6.4 LLM abstraction

Single-vendor v1, but behind a Protocol so swap is a one-liner later:

```python
class LLM(Protocol):
    async def call(self, messages, tools) -> LLMResponse: ...
    async def synthesize(self, state) -> str: ...

class GrokLLM:
    """xAI Grok via OpenAI-compatible API at https://api.x.ai/v1."""
    model: str = "grok-4.20"
    ...
```

Implementation uses the `openai` async client pointed at xAI's endpoint (xAI's API is OpenAI-compatible for chat completions + tool use).

### 6.5 `apify_twitter` tool — X-content retrieval via Apify

X-content retrieval happens through a dedicated Apify scraper actor. The actor is invoked by `ApifyTwitterTool`, a LangChain `BaseTool` that takes either a `handle` (fetch one user's profile + recent tweets) or a `search_query` (search tweets across X), runs the actor synchronously, and returns the dataset items as `(content, artifact)`.

> Historical note: earlier drafts used Grok's Live Search via Chat Completions (deprecated 2025-12-15) and then xAI's Responses API + `x_search` server-side tool. Both paths required a Grok-side LLM call and special token-accounting plumbing. We now route X scraping through Apify like the other social-platform tools, which gives us uniform vendor handling, no special LLM-bypass case, and an actor we can swap independently of any xAI API change.

```python
class ApifyTwitterTool(BaseTool):
    name = "apify_twitter"
    args_schema = _TwitterInput   # {handle?, search_query?, max_items}
    response_format = "content_and_artifact"

    async def _arun(self, handle=None, search_query=None, max_items=20):
        if not handle and not search_query:
            raise ValueError("apify_twitter requires either handle or search_query")
        run_input = {"maxItems": max_items}
        if handle:
            run_input["twitterHandles"] = [handle]
        else:
            run_input["searchTerms"] = [search_query]
        artifact = await _run_actor(self.client, self._actor_id, run_input)
        content = json.dumps({"items": artifact["items"]}, default=str)[:4000]
        return content, artifact
```

**Why Apify rather than Grok's `x_search`?** `x_search` returns a *model-synthesized answer* over content xAI's tool decided to fetch — opaque, non-deterministic per call, and we don't directly get the underlying tweets as structured records. Apify's scraper returns the actual tweet objects (author, text, timestamps, engagement counts), which is what the entity graph and final report need. Apify also lets us swap the actor for a different one without changing the rest of the pipeline.

**Routing.** The main agent's system prompt (built in `prompts.py`) emits explicit rules per enabled tool, including: *"For ANY X (Twitter) content, use `apify_twitter` — pass `handle` for a profile fetch or `search_query` for a tweet search. Don't use `tavily_search` for X content; X's public surface is poorly indexed by general web search."*

**Default actor.** `apidojo/twitter-scraper-lite` is a popular maintained Apify actor; `actor_id` is configurable. If a different actor is preferred, the input schema may need adjustment in the tool's `_arun`.

### 6.6 Rate limiting

Scope: target-site rate limits only. Vendor-side quotas (Tavily / Apify / xAI) are handled by accepting 429 responses and backing off — no client-side semaphores for vendor traffic.

The concern: some tools make outbound HTTP requests from our server's IP *directly to many target sites*. If we fan out aggressively (either within a single scan or across many concurrent scans in the future web app), those target sites will 429 us, captcha us, or IP-ban us.

**Which v1 tools hit target sites directly:** `maigret` only. Every other v1 tool goes through a vendor (Tavily, Apify, xAI) whose own proxy/IP infrastructure absorbs the target-site pressure.

**Mitigations, stacked, specifically for `maigret`:**

1. **Internal throttle inside the tool.** Maigret is invoked with a conservative concurrency cap (default `max_connections=15`), short per-site timeout, and one retry. The agent can override to be even more conservative via the tool's input schema.

2. **Process-wide cap on concurrent invocations.** A dedicated `asyncio.Semaphore` — not for vendor quota, purely for target-site politeness:
   ```python
   TOOL_LIMITS = {
       "maigret": asyncio.Semaphore(2),   # at most 2 Maigret runs in flight
   }
   ```
   This is the cap that matters in a multi-user deployment: no matter how many scans are running, only N Maigret fanouts happen from our IP at once.

3. **Proxy support (config knob now, active in prod).** `MaigretTool` accepts a `proxy_url: str | None` in `ScanConfig.tool_options["maigret"]`, defaulting to `None` for dev. In production this is populated with a rotating-proxy provider (Bright Data / ScraperAPI / similar), which is the real fix for scale. Wiring the knob now means no interface change later.

4. **Site-list filter.** `MaigretTool` accepts `sites_filter: list[str] | None` in its input schema — when populated, restricts checks to a subset rather than all ~3000 sites. Unused by default; available for the agent to request a narrow check when a full fanout is overkill.

**Rule for future tools that scrape directly** (e.g. if we later add `holehe`, or self-hosted Playwright scrapers): the tool declares itself direct-scraping, gets a `TOOL_LIMITS` entry, accepts a `proxy_url` knob, and documents its default internal concurrency. Tools that route through a vendor don't need any of this.

### 6.7 Storage

One JSON file per scan at `{scans_dir}/{scan_id}.json`. Structure:

```json
{
  "scan_id": "uuid",
  "created_at": "ISO8601",
  "completed_at": "ISO8601",
  "status": "done | failed",
  "subject": "free-form natural language description",
  "extracted_identifiers": { /* the agent's parse of the subject string: emails, handles, etc. */ },
  "config": { /* ScanConfig */ },
  "tool_calls": [
    {
      "turn": 1,
      "tool": "tavily_search",
      "input": { "query": "..." },
      "output": { /* parsed result */ },
      "raw": { /* verbatim vendor response */ },
      "started_at": "...", "completed_at": "...",
      "cost_usd": 0.01,
      "error": null
    }
  ],
  "report": { /* final structured report from the LLM */ },
  "tool_cost_usd": 0.xx,
  "llm_cost_usd": 0.xx,
  "llm_input_tokens": 1234,
  "llm_output_tokens": 567,
  "total_cost_usd": 0.xx,
  "duration_sec": 123
}
```

The `raw` field guarantees nothing is silently dropped even if the LLM's synthesis missed it. Sensitive fields (e.g. the subject's own identifiers) are left in the file — this is the caller's data about themselves, not a secret to us.

### 6.8 Limits / budget

`ScanConfig` carries three hard caps checked before each tool dispatch:
- `budget_usd: float` — running sum of **LLM token cost + tool call cost** for this scan. See §6.8.1 for how the two components are computed.
- `max_tool_calls: int` — absolute cap on tool calls per scan.
- `max_wall_clock_sec: int` — hard stop on elapsed time.

When any cap is hit mid-scan, the loop stops dispatching new tools, calls the LLM one last time asking for a final synthesis from what it has, and writes the scan record.

#### 6.8.1 Cost accounting

Total scan cost has two sources and both must count against `budget_usd`, otherwise the budget silently under-bounds spend in a ReAct loop that calls the LLM on every turn.

**Tool cost.** Each tool declares an `est_cost_usd_per_call`. The scan's running tool cost is the sum across all dispatched calls (including failed ones — the vendor still charged). All v1 tools route through vendor APIs that don't share token usage with the main agent's LLM, so there is no double-counting between tool cost and LLM cost.

**LLM cost.** Every ReAct turn makes an LLM call. A LangChain callback captures `input_tokens` and `output_tokens` from each response's `usage_metadata` / `token_usage` block and accumulates them on `ScanState`. The cost is computed from a pricing table on `ScanConfig`:

```python
class LLMPricing(BaseModel):
    input_per_mtok_usd: float = 2.0     # grok-4.20 default (2026-04)
    output_per_mtok_usd: float = 6.0    # grok-4.20 default (2026-04)
```

These defaults are informed by xAI's public pricing page at the time of writing; callers should override when model or pricing changes. The callback is registered on both the main ReAct agent and the synthesis call, so the budget is enforced end-to-end.

**`total_cost_usd`** on `ScanState` is `tool_cost_usd + llm_cost_usd`. `should_stop()` compares this against `budget_usd`. The per-scan JSON output stores all three components plus the raw token counts so cost breakdowns are auditable.

### 6.9 Logging

Structured logs via `structlog` to stderr. One log line per: scan start, scan stop (with reason), tool call start, tool call end, tool call error. Log lines include `scan_id` but **never** include subject PII — only tool name, vendor, duration, cost, outcome.

## 7. v1 tool list

| Tool | Vendor | Tier | Est cost/call | Notes |
|---|---|---|---|---|
| `tavily_search` | tavily | free-tier | ~$0.008/call | Basic search = 1 credit @ $0.008/credit (PAYG). |
| `tavily_extract` | tavily | free-tier | ~$0.008/call | 1 credit per 5 URLs (basic depth). |
| `maigret` | maigret | free | $0 | Local lib; username → accounts across ~3000 sites |
| `apify_instagram` | apify | paid | ~$0.03/call | `apify/instagram-scraper` @ $1.50 / 1k results; default 1 profile + 20 posts. |
| `apify_linkedin` | apify | paid | ~$0.01/profile | `dev_fusion/linkedin-profile-scraper` @ $10 / 1k profiles. |
| `apify_twitter` | apify | paid | ~$0.02/call | `apidojo/twitter-scraper-lite` ≈ $0.016 per standard query (covers ~40 tweets). Two input modes: `handle` (profile + recent tweets) or `search_query` (search across X). |

Paid tools default to *disabled* in `ScanConfig` unless the caller explicitly enables them (and the corresponding API key is set in env).

## 8. Public API of the package

```python
# osint/__init__.py exports:

class ScanConfig(BaseModel):
    enabled_tools: set[str] = {"tavily_search", "tavily_extract", "maigret"}
    budget_usd: float = 5.0
    max_tool_calls: int = 30
    max_wall_clock_sec: int = 600
    tool_concurrency: dict[str, int] = Field(default_factory=default_tool_concurrency)
    tool_options: dict[str, dict] = Field(default_factory=dict)   # e.g. {"maigret": {"proxy_url": ...}}

class ScanResult(BaseModel):
    scan_id: str
    report: dict                # LLM's final structured report
    tool_calls: list[ToolCall]  # full verbatim log
    total_cost_usd: float
    duration_sec: float
    path: Path                  # where the JSON was written

async def scan(
    subject: str,                           # free-form natural-language description
    config: ScanConfig = ScanConfig(),
    llm: LLM | None = None,
    scans_dir: Path = Path("./scans"),
) -> ScanResult: ...
```

A small CLI wrapper (`python -m osint.cli scan "Jane Doe, Stuyvesant '08, jane@example.com, @jdoe_nyc"`) is included for manual invocation. Reads from stdin if no argument is passed, so multi-paragraph descriptions pipe in cleanly.

## 9. Configuration

All secrets read from env vars. Required when the corresponding tool is enabled:

- `XAI_API_KEY` — always required (main agent LLM)
- `TAVILY_API_KEY` — required if `tavily_*` tools enabled
- `APIFY_TOKEN` — required if `apify_*` tools enabled

Missing key for an enabled tool is a `ScanConfigError` at `scan()` entry, before any API calls.

## 10. Forward path (not v1)

Documented here because the v1 design is chosen to enable them cleanly:

- **HTTP surface.** Wrap `scan()` in ~20 lines of FastAPI. Since `scan()` is already async, FastAPI can `await` it directly — no thread-bridging needed.
- **Per-user rate limits.** Swap the `RateLimiter` implementation to Redis-backed token bucket; pass `user_id` through `ScanContext`. Tool code unchanged.
- **Cross-scan caching.** Introduce a `Cache` interface keyed by `(tool_name, normalized_input_hash)`. Wrap `invoke_tool`. Tool code unchanged.
- **Additional tools.** Add a file under `osint/tools/`. No orchestrator or protocol changes. Candidates for v2: `holehe`, `ghunt`, `hibp_email`, `github_profile`, `proxycurl_linkedin`, `apify_tiktok`, `apify_facebook`, SpiderFoot wrap.
- **Second LLM vendor.** Add a new `LLM` implementation (e.g. Claude). Callers pass the one they want; the abstraction is already there.
- **Entity resolution as a separate pass.** Build on top of `tool_calls` (already verbatim-logged) without needing to re-run tools.

## 11. Explicit deferrals

- Entity graph, typed events, provenance DAG — deferred; raw `tool_calls` log is enough for v1.
- Resumability after crash — deferred; v1 scans are atomic, a crash means re-run.
- Observability beyond structlog — deferred.
- PimEyes, DeHashed, IntelX, Bright Data Web Unlocker, SpiderFoot wrap — deferred.
- Web app, auth, multi-tenant — entirely out of scope for this spec.

## 12. Open questions

None. All decisions locked for v1.
