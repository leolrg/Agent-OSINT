# Agent v2 — Lead-Queue architecture

**Date:** 2026-04-26
**Status:** Approved (brainstorming complete; ready for implementation plan)
**Supersedes:** Nothing — coexists with v1.

## Goals

- **Depth.** Maximally thorough single-profile reports — every social handle, project, artifact, behavioral signal, network tie that the open web reveals about the subject.
- **Generality.** Works across subject archetypes, not just the CN-name + GZFLS + crypto subjects v1 was tuned on. Subjects with sparse online footprint, Western names, non-crypto context, low-signal industries should not silently produce thin reports.

## Non-goals

- **Network expansion.** v2 produces a single-profile report. Recursive sub-scans of associates are out of scope. (Associates surface as facts about the subject, not as their own profiles.)
- **Reproducibility-as-primary.** Apify Google search is now reliable enough that handle-discovery reproducibility is a downstream property, not the headline goal.
- **Speed.** v2 is a deep-dive mode, not an interactive scan. Target wall-clock 30–60 min per scan.

## Budget envelope

| Resource | v1 default | v2 default |
| --- | --- | --- |
| `budget_usd` | 5.0 | 5.0 |
| `max_tool_calls` | 30 | 200 |
| `max_wall_clock_sec` | 600 (10 min) | 3600 (60 min) |
| `max_verifier_iterations` | n/a | 3 |

## Architecture

Single ReAct agent per scan, but the *scan* is a queue-driven main loop. The agent processes one **lead** at a time; each lead generates new leads or terminal findings. The investigation shape is data-driven by what's actually surfacing about the subject — no pre-defined dimensions, no bucketing.

```
seed (identity-lock lead, priority=100, depth=0)
  ↓
main loop:
  while queue not empty AND not state.should_stop():
    lead = queue.pop()
    findings, new_leads = await processor.process_one(lead, …)
    state.findings.extend(findings)
    queue.push(new_leads)  # dedup by description hash
    state.leads_log.append(lead)
  ↓
synthesizer (1 LLM call → draft report)
  ↓
verifier loop (≤ max_verifier_iterations):
  result = await verifier.verify(draft, findings, leads_log)
  if result.satisfied: break
  queue.push(result.new_leads)
  drain main loop again
  re-synthesize
  ↓
final report
```

**Key property:** the queue is empty when there is genuinely nothing left to investigate, not when the LLM decided it was done. A subject with rich online presence fills the queue with leads naturally; a subject with thin presence drains the queue and the scan finishes early without burning the full budget.

## Multiple agent versions

v1 is preserved unchanged. The codebase is restructured so each agent is a sub-package under `osint/agents/`, both with the same shape:

```
osint/
  run.py                    # thin dispatcher: build state+tools, look up agent, call it, persist
  agents/
    __init__.py             # AGENTS = {"react_v1": ReactV1Runner, "leadqueue_v2": LeadQueueV2Runner}
    base.py                 # AgentRunner protocol
    react_v1/
      __init__.py           # entrypoint: ReactV1Runner
      runner.py             # multi-pass logic (moved from osint/run.py)
      prompts.py            # SYSTEM_TEMPLATE, DEEPEN_TEMPLATE, _ROUTING_RULES, parse_report
                            # (moved from osint/prompts.py)
    leadqueue_v2/
      __init__.py           # entrypoint: LeadQueueV2Runner
      queue.py              # Lead, LeadQueue (priority + dedup)
      processor.py          # process_one_lead(lead, …) -> findings + new_leads
      synthesizer.py        # findings -> draft report
      verifier.py           # report + findings -> {satisfied, new_leads}
      prompts.py            # processor / synthesizer / verifier prompt templates
```

**Shared modules stay at `osint/` top level** because they are genuinely cross-agent: `state.py`, `storage.py`, `types.py`, `errors.py`, `log.py`, `llm_cost.py`, `capped_tool.py`, `tools/`. Both runners use the same `CappedTool` wrappers, the same `ScanState`, and the same `write_scan_json` / `write_scan_markdown` for output.

**Selection:** new field `ScanConfig.agent_version: Literal["react_v1", "leadqueue_v2"] = "react_v1"`. Default stays v1 — every existing CLI invocation behaves identically. New `--agent` CLI flag opts into v2.

## Components

### `Lead` (Pydantic model)

```python
class Lead(BaseModel):
    id: str                     # unique
    kind: str                   # informal tag, e.g. "investigate_handle", "extract_url"
                                # used for logging + dedup, NOT branching
    description: str            # natural-language instruction the processor LLM reads
    priority: int               # higher = process first
                                # init: identity=100, snippet-derived=50, speculative=20
    depth: int                  # how many lead-generations deep (root=0)
                                # used to discount priority of deep leads at push time
    parent_lead_id: str | None
    created_at: datetime
```

### `LeadQueue`

In-memory priority queue with a seen-set for dedup (hash of `description.lower().strip()`). Persisted to `ScanState.leads_log` as leads are popped, so an audit trail survives the scan.

```python
class LeadQueue:
    def push(self, lead: Lead) -> bool: ...       # returns False if dedup'd
    def pop(self) -> Lead | None: ...
    def is_seen(self, lead: Lead) -> bool: ...
    def empty(self) -> bool: ...
```

### `Finding` (Pydantic model)

```python
class Source(BaseModel):
    tool_call_id: str
    snippet_quote: str          # the literal text from the tool result that supports the claim

class Finding(BaseModel):
    id: str
    claim: str                  # natural-language fact
                                # e.g. "subject's IG handle is simonwen.eth"
    evidence: list[Source]      # at least one
    confidence: Literal["high", "medium", "low"]
    lead_id: str                # which lead surfaced this
    tags: list[str]             # open-set, e.g. ["handle", "instagram"]
                                # for synthesizer grouping, not enforcement
```

### Processor (`processor.process_one`)

One LLM call per lead. Internally runs a tiny ReAct mini-loop (≤5 turns of tool calls) bounded so a single lead can't consume the whole budget.

**Inputs:** subject string, the lead, a compact summary of `state.findings` so far (just `claim` strings, deduplicated), the tool list.

**Output (structured JSON):**

```json
{
  "findings": [{ "claim": "...", "evidence": [...], "confidence": "...", "tags": [...] }],
  "new_leads": [{ "kind": "...", "description": "...", "priority": 50 }]
}
```

### Synthesizer (`synthesizer.synthesize`)

One LLM call. Reads ALL findings (claim + evidence) plus the subject string. Output: prose report following v1's existing Output Format + tail JSON `extracted_identifiers` block — same contract as v1, parsed by the existing `parse_report` helper. Reuses v1's prompt structure for the report sections so the user-facing output format is unchanged.

### Verifier (`verifier.verify`)

One LLM call per iteration. Reads the draft report, the findings inventory, and the list of leads already processed.

**Output:**

```json
{
  "satisfied": false,
  "gaps": ["No source for 'subject attended NYU 2024' claim",
           "Report mentions GitHub but no actual repos found"],
  "new_leads": [
    { "kind": "verify_employer", "description": "Find evidence subject attended NYU. Search NYU directory, ID by school+year, ...", "priority": 80 }
  ]
}
```

If `satisfied=true`, the verifier loop terminates. If false, `new_leads` get pushed to the queue (priority bumped to re-process before older lower-priority leads), the main lead-processing loop drains again, the synthesizer runs again on the augmented findings.

### `ScanState` extensions

```python
@dataclass
class ScanState:
    # existing fields (scan_id, subject, config, tool_calls, messages, …)
    findings: list[Finding] = field(default_factory=list)         # NEW
    leads_log: list[Lead] = field(default_factory=list)           # NEW (every lead processed, for audit)
    verifier_iterations: int = 0                                  # NEW
```

These fields are unused by v1 (their default factories produce empty values), so v1's serialized scan JSON is unchanged.

## Data flow (per scan)

1. Dispatcher (`osint/run.py`) builds `ScanState`, tools, cost callback.
2. Dispatcher resolves `runner = AGENTS[config.agent_version](...)`.
3. Dispatcher calls `await runner.run(subject, config, llm, state, tools, cost_cb)`.
4. (v2 only) Runner seeds queue with the identity-lock lead, runs main loop, synthesizes, verifier loop, re-synthesizes if needed.
5. Runner returns `(parsed_report_dict, stop_reason)` to the dispatcher.
6. Dispatcher persists scan JSON + Markdown via existing `write_scan_*` functions.

## Configuration

```python
class ScanConfig(BaseModel):
    # existing fields (budget_usd, max_tool_calls, max_wall_clock_sec, …)
    agent_version: Literal["react_v1", "leadqueue_v2"] = "react_v1"   # NEW
    max_verifier_iterations: PositiveInt = 3                          # NEW (v2-only)
```

CLI:

```
python -m osint.cli scan "subject string" --agent leadqueue_v2 [other flags]
```

When `--agent` is omitted, the default `react_v1` runs — identical to today.

## Error handling

| Failure | Handling |
| --- | --- |
| Processor LLM call fails (5xx, malformed structured output) | Retry once. On second failure: log a synthetic error tool-call record, mark lead as failed in `leads_log`, consume it (do NOT requeue), continue to next lead. |
| Tool call fails inside a processor mini-loop | Already absorbed by `CappedTool` (returns error-content string). No new handling. |
| Processor proposes a duplicate lead | Dedup at push: `description.lower().strip()` hash matches an entry in `seen` → silent reject. |
| Verifier returns malformed JSON | Retry once. On second failure: treat as `satisfied=true` and accept the current draft. |
| Synthesizer emits empty content (Grok 0-token reasoning bug) | Reuse existing `EMPTY_FINAL` fallback path: second synthesis call with stripped prompt. |
| Budget / wall-clock cap hit mid-loop | Synthesize on whatever findings exist; write partial report; `stop_reason = BUDGET | WALL_CLOCK`. Verifier loop is skipped. |
| Verifier oscillation (5+ iterations of unsatisfied with shifting gaps) | Hard-bounded by `max_verifier_iterations`. |
| Identity-lock can't reach 3 cross-references | Identity finding gets `confidence=low`; subsequent findings inherit `confidence ≤ medium`; final report includes a "Subject identification: low confidence" disclaimer. Scan still completes. |
| Empty queue right after seed (gibberish subject) | Synthesizer runs on identity-lock findings only; verifier gets one chance to propose leads. |
| Process crash / unexpected exception | Dispatcher's `try/except` writes a `status="failed"` JSON record, preserving partial state. |

## Testing

### Unit

- `queue.py`: priority ordering; dedup-on-push; `is_seen` after pop; empty/pop semantics
- `processor.py`: input-summary compactness (truncates correctly when findings > 100); output parsing well-formed and malformed; one-retry-then-skip
- `synthesizer.py`: prompt builds with empty findings AND large finding lists; output parses through existing `parse_report`
- `verifier.py`: parses `{satisfied, gaps, new_leads}`; treats malformed-after-retry as `satisfied=True`
- `agents/__init__.py`: registry has both entries; dispatcher routes correctly per `config.agent_version`

### Integration (with `BindableFakeModel`)

- v2 happy path: mock processor returns one lead → one finding → no new leads → synth → verifier(satisfied) → report
- v2 lead expansion: mock processor returns 3 new leads each iteration; assert queue grows then drains
- v2 verifier loop terminates: verifier returns `satisfied=False` 5 times; assert exactly `max_verifier_iterations` iterations occur; final report still produced
- v2 verifier dedup: verifier proposes a lead identical to one already in `leads_log` — push is rejected
- Dispatcher: `agent_version="react_v1"` runs ReactV1Runner; `="leadqueue_v2"` runs LeadQueueV2Runner

### Regression for v1

- All existing tests in `tests/test_run.py`, `tests/test_prompts.py`, `tests/test_storage.py`, `tests/test_types.py`, etc. continue to pass after the file moves into `osint/agents/react_v1/`.
- A scan with default config produces a JSON record byte-equivalent in shape to today's output (no new keys; the v2-only `ScanState` fields serialize as empty lists / 0).

### Live smoke (manual, not CI)

- Run v2 on Simon (`Simon 温行健, 高中在广州外国语学校`) and Allison (`Allison 王嘉琪 NYU Stern`); confirm at least the union of v1 best-case findings lands in the report; spot-check that `leads_log` is populated and persisted.

## Phasing

This spec is the architecture. The implementation plan (next document) will sequence the work, but at a high level:

1. Refactor: introduce `osint/agents/`, move v1 logic, update imports/tests until all green. (No behavior change.)
2. Add `ScanConfig.agent_version` + dispatcher branch + CLI flag.
3. Implement v2 components in order: Queue → Processor → Synthesizer → Verifier → Runner glue.
4. Add unit + integration tests at each step (TDD-style; no v2 code without a failing test first).
5. Live smoke on Simon + Allison.
6. Document v2 in README.

## Open questions deferred to implementation

- Should the processor's mini-loop reuse `create_react_agent` or be hand-rolled? (Tradeoff: less code vs more control over the per-lead budget.)
- What's the right `max_tool_calls` per lead inside the processor? (Initial guess: 5; tune from live traces.)
- How compact should the findings-summary fed to the processor be? Likely truncated/summarized once findings > 50 items.
