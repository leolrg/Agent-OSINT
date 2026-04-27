# critic_react_v3 — Critic-Driven Parallel ReAct

- **Date:** 2026-04-27
- **Status:** design (brainstormed, awaiting plan)
- **Author:** brainstorm session
- **Supersedes:** none. Adds a third agent alongside `react_v1` and `leadqueue_v2`. Does not modify either.

## Motivation

The two existing agents under-utilize the tool budget and produce shallow reports.

Evidence — leadqueue_v2 scan `0c77db948bb24e20bd560c7c1d050fc0` (subject: an Instagram-discovered person):

- 31 tool calls in ~11 minutes; 21 of 31 were `web_search`, 1 `apify_instagram`, 0 `apify_linkedin`, 0 `apify_twitter` (the latter two were enabled).
- Sequential, not parallel: ~21 s per call wall-clock.
- 6 leads ever processed. The processor's hardcoded `max_processor_tool_calls=5` ceiling caps per-lead depth.
- Concrete identifiers were left un-pivoted: `email fc202817@bunka-fc.ac.jp`, `xhs_user_id 62bdc417b16cb61d163c599d`, `douyin_hash MS4w…`, brand collabs, IG-post mentions/hashtags. Each was a free pivot the agent skipped.

The user has tool-call credits available and a goal-agnostic surface area: the agent should serve coffee-chat prep, sales outreach, reconnect-with-old-contact, dossier, and similar use cases — without specialised code per use case.

## Goals

1. Burn the available tool-call budget on recall, not LLM thinking, by emitting parallel tool calls per turn instead of sequential singletons.
2. Force depth via an external critic that re-engages the agent until the goal is met or rejection cap is hit.
3. Stay fully general: no atom taxonomy, no pivot recipe catalog, no specialist roster, no archetype-specific prompts.
4. Goal-conditioned output: the agent's behavior and report shape adapt to user intent (coffee chat vs dossier) via free-form goal text and a small preset library.
5. Zero impact on `react_v1`, `leadqueue_v2`, `xai_multiagent_v1`. Backwards-compatible `ScanState` and JSON output.

## Non-goals

- Any typed atom store, atom-extraction pass, or pivot engine. Explicitly rejected during brainstorming as too domain-specific.
- A specialist swarm (Aliases, Network, Brands, Education, ...). Rejected as forensics-biased and culturally over-fit.
- A separate brief-generation LLM call. Rejected — the goal text alone goes into the system prompt.
- A preset-templated final-report renderer. Rejected — the agent writes the final report itself; identifiers are extracted via a JSON envelope at the end (same pattern as v1).
- New tools. The roster is unchanged from v1/v2 (`web_search`, `web_extract`, `maigret`, `apify_instagram`, `apify_linkedin`, `apify_twitter`). Adding tools later is purely additive — register and the system-prompt tool block updates.

## Architecture overview

Three disciplines layered on a single LangGraph ReAct agent:

1. **Parallel tool calls per turn.** System prompt mandates batching independent tool calls into one assistant message. LangGraph executes them concurrently. This is the primary mechanism that converts the credit surplus into recall.
2. **Open-question ledger.** Every assistant turn begins with a JSON block listing `open`, `answered`, `dropped` questions the agent is tracking. The orchestrator parses it. The agent may not emit `done` while `open` is non-empty; if it does, the orchestrator auto-rejects with a synthetic user message and resumes.
3. **Adversarial external critic.** When the agent emits `done` with an empty `open` list, a separate LLM call (no tools) reviews the goal, the draft report, and the tool-call summary. It returns `ACCEPT` or `REJECT` plus a list of gaps. On reject, the orchestrator appends a "reviewer flagged these gaps" user message and resumes the same agent (full context preserved). Caps at `max_critic_rejections`.

No atom store, no pivot engine, no roster, no waves, no brief.

### Inputs

- `subject: str` — free-form, same as today.
- `goal: str` — free-form, optional. Default `""`.
- `preset: Literal["coffee_career","coffee_personal","reconnect","sales_outreach","dossier","general"]` — default `"general"`.

Both are honored: the preset preamble and the goal text are concatenated into the system prompt, in that order. `goal` may be empty; `preset` always resolves to one of the six values (default `general`). If `goal` is empty and `preset` is `general`, the system prompt contains only the general preamble — equivalent to a vague "investigate this person" instruction.

### Outer-loop pseudocode

```python
async def run(*, subject, state, llm, tools, cost_cb):
    config = state.config
    system = build_system_prompt(
        subject=subject,
        goal=config.goal,
        preset=config.preset,
        tools=tools,
    )
    messages = [SystemMessage(system), HumanMessage("Begin.")]

    rejections = 0
    while not state.should_stop()[0] and rejections <= config.max_critic_rejections:
        agent = create_react_agent(llm, tools, prompt=None)
        try:
            result = await asyncio.wait_for(
                agent.ainvoke(
                    {"messages": messages},
                    config={
                        "recursion_limit": config.max_recursion_per_engagement,
                        "callbacks": [cost_cb],
                    },
                ),
                timeout=config.max_wall_clock_sec,
            )
        except (ScanStopped, asyncio.TimeoutError, GraphRecursionError) as e:
            # Reuse v1's cap-cut synthesizer to produce a partial report
            # from the message log so the user gets *some* output even on
            # preemption. v1 path: osint.agents.react_v1.runner._synthesize.
            stop = StopReason.WALL_CLOCK if isinstance(e, asyncio.TimeoutError) \
                else StopReason.MAX_CALLS if isinstance(e, GraphRecursionError) \
                else StopReason(e.reason)
            synth_text, synth_msgs = await react_v1_synthesize(
                llm, subject, state, stop.value, cost_cb,
            )
            state.messages.extend(serialize_messages(synth_msgs))
            return parse_report(synth_text), stop

        messages = result["messages"]
        last_ai_text = extract_last_ai_text(messages)
        ledger = parse_ledger(last_ai_text)

        if ledger.open:
            messages.append(HumanMessage(
                f"You stopped with open questions: {ledger.open}. "
                f"Continue investigation; you may use any tools."
            ))
            continue

        verdict = await critic(
            subject=subject, goal=config.goal, preset=config.preset,
            draft=last_ai_text, tool_summary=summarize_tool_calls(state.tool_calls),
            llm=llm, cost_cb=cost_cb,
        )
        if verdict.accept:
            return parse_report(last_ai_text), None
        rejections += 1
        if rejections > config.max_critic_rejections:
            return parse_report(last_ai_text), StopReason.CRITIC_EXHAUSTED
        messages.append(HumanMessage(
            f"A reviewer flagged these gaps:\n- " + "\n- ".join(verdict.gaps) +
            "\n\nAddress each. Use any tools. Update your open-question ledger."
        ))

    return parse_report(extract_last_ai_text(messages)), state.should_stop()[1]
```

### System prompt — required elements

The exact prompt is implementation-time. It must contain, at minimum:

1. **Goal block** — preset preamble plus user goal text.
2. **Tool list block** — name, one-line usage, per-call cost. Built dynamically from the registered tools.
3. **Parallelism rule** — verbatim: *"When 2+ tool calls are independent, emit them as a single batch in one assistant message. Sequential single calls when batching is possible is a defect."*
4. **Ledger rule** — every assistant message begins with a fenced JSON block of shape `{"open": [...], "answered": [...], "dropped": [...]}`. May only emit a `done` signal (final report) when `open` is empty.
5. **Stop discipline** — never emit done if a finding contains a concrete identifier (email, handle, url, platform user id) that has not been followed up on. This is the model-side counterpart of the orchestrator's auto-rejection.
6. **Final-report contract** — at done, emit the full free-form report followed by exactly one fenced JSON block keyed `extracted_identifiers` with the same shape as v1 (emails, usernames, urls, name_variations, schools, employers, locations, phones, addresses). Identifier extraction reuses `osint.agents.react_v1.prompts.parse_report`.

### Open-question ledger format

```json
{
  "open": ["What is the subject's current employer?", "..."],
  "answered": ["Subject is based in Tokyo (web_search 3)", "..."],
  "dropped": ["Marriage status — unanswerable from public sources", "..."]
}
```

Each item is free-form text. Items move from `open` to `answered` (with a brief evidence pointer) or `dropped` (with a brief reason). Parser is a regex for the first fenced JSON block in the assistant message; failure → empty ledger.

### Critic prompt and parsing

```
You are reviewing whether an investigation has met its goal.

GOAL: {goal_text}
PRESET HINT: {preset_short_hint}
DRAFT REPORT: {final_assistant_text}
TOOLS USED (count by name): {tool_call_histogram}

Decide: accept the draft, or reject with specific gaps the investigator
should address. A gap is something the goal needs that the draft does
not currently support, OR a concrete identifier in the draft (email,
handle, url, id) that was never followed up on.

Respond in this exact form:

VERDICT: ACCEPT | REJECT
GAPS:
- (if REJECT, one bullet per gap; otherwise omit this section)
```

Parser: regex for `VERDICT:` line, bullets after `GAPS:`. Parse failures → treat as ACCEPT (avoid infinite loops on parser fragility). The critic does not introduce or evaluate against any taxonomy; it operates on free text only.

### Termination matrix

| Stop reason | Trigger | Output |
|---|---|---|
| `CRITIC_ACCEPTED` (new) | critic returns ACCEPT | last draft, parsed via v1's `parse_report` |
| `CRITIC_EXHAUSTED` (new) | rejections > `max_critic_rejections` | last draft, parsed |
| `BUDGET` | `state.total_cost_usd` ≥ budget | cap-cut synthesis from message log (v1 path) |
| `MAX_CALLS` | tool calls hit cap | cap-cut synthesis |
| `WALL_CLOCK` | wall-clock cap hit | cap-cut synthesis |
| `EMPTY_FINAL` | last AI message empty | cap-cut synthesis |

`CRITIC_ACCEPTED` and `CRITIC_EXHAUSTED` are added to the existing `StopReason` enum; cap-cut paths reuse the v1 fallback synthesizer (`osint.agents.react_v1.runner._synthesize`).

### Presets

```python
PRESETS = {
    "coffee_career": (
        "I'm preparing for a coffee chat with this person, focused on their career. "
        "Find their current role and employer, recent shipped work or projects, "
        "any public talks/posts/papers worth referencing, and shared interests "
        "that might come up. Flag anything sensitive to avoid (recent layoff, "
        "controversy, loss). Skip family, addresses, and history older than ~5y "
        "unless directly relevant."
    ),
    "coffee_personal": (
        "I want to know this person better as a friend or new acquaintance. "
        "Find their hobbies, interests, communities they're part of, and recent "
        "public posts I could react to. Skip employment-financial details, "
        "addresses, and anything that feels invasive."
    ),
    "reconnect": (
        "I want to reconnect with this person after time apart. Find what "
        "they've been doing recently — new role, new city, life events, "
        "projects — so I can open the conversation naturally."
    ),
    "sales_outreach": (
        "I'm preparing outreach to this person about a business matter. "
        "Find their company, role, recent public communications, mutual "
        "connections, and topics they care about that I can reference warmly."
    ),
    "dossier": (
        "Build a comprehensive dossier. Be thorough across identity, career, "
        "education, online footprint, network, geography, and history. "
        "Surface concrete identifiers and follow up on each."
    ),
    "general": (
        "Investigate this person with whatever lens makes sense from the "
        "subject description and any user-provided goal."
    ),
}
```

A short preset hint (1 sentence) is also exposed for the critic prompt.

### Config additions

`osint.types.ScanConfig` gains:

```python
goal: str = ""
preset: Literal[
    "coffee_career", "coffee_personal", "reconnect",
    "sales_outreach", "dossier", "general"
] = "general"
max_critic_rejections: PositiveInt = 3
max_recursion_per_engagement: PositiveInt = 50
```

`agent_version` accepts `"critic_react_v3"` as a fourth value alongside the existing three.

### CLI flags

In `osint/cli.py`:

```
--agent critic_react_v3
--preset {coffee_career|coffee_personal|reconnect|sales_outreach|dossier|general}
--goal "free-form text"
--max-critic-rejections N
--max-recursion-per-engagement N
```

All optional; missing flags fall through to `ScanConfig` defaults.

### Tool roster

Unchanged. Whatever is in `enabled_tools` works — the agent has no taxonomy that ties it to specific tools. Adding a tool later (Wayback, Reddit JSON, GitHub events) is purely additive: register it and the system-prompt tool block updates automatically.

### Files and module layout

New (no changes to v1/v2/xai modules):

```
osint/agents/critic_react_v3/__init__.py
osint/agents/critic_react_v3/runner.py        # outer loop, ~150 LOC
osint/agents/critic_react_v3/prompts.py       # system prompt builder, critic prompt, ledger parser
osint/agents/critic_react_v3/critic.py        # single-call critic
tests/agents/critic_react_v3/test_runner.py
tests/agents/critic_react_v3/test_prompts.py
tests/agents/critic_react_v3/test_critic.py
```

Wiring:

- `osint/run.py` — add `"critic_react_v3"` to `AGENTS` dispatch.
- `osint/state.py` — add `CRITIC_ACCEPTED` and `CRITIC_EXHAUSTED` to `StopReason`.
- `osint/types.py` — extend `ScanConfig` as above.
- `osint/cli.py` — add the new flags and pass them through to `ScanConfig`.
- `README.md` — add a fourth bullet under "Agents".

### Backwards compatibility

- `ScanState`, `ScanResult`, and the JSON output schema are unchanged. The critic and ledger are agent-internal; they do not appear in persisted output beyond what already lands in `state.messages`.
- `react_v1` and `leadqueue_v2` reuse `parse_report` and `_synthesize` from v1; this spec also reuses them. No refactors required.
- New `StopReason` enum values are additive; existing serialization via Pydantic `model_dump` handles them transparently.

### Testing strategy

Unit:

- `parse_ledger` — well-formed JSON, fenced JSON block, malformed (returns empty), missing block.
- `parse_critic_verdict` — ACCEPT, REJECT with bullets, REJECT with no bullets, malformed (treats as ACCEPT).
- `build_system_prompt` — preset-only, goal-only, both, neither.

Integration with `FakeMessagesListChatModel` (same harness as v2 tests):

- Happy path: agent emits `done` with empty ledger → critic ACCEPT → returns parsed report.
- Ledger non-empty retry: agent emits `done` with `open: [...]` → orchestrator appends synthetic user message → second engagement empties ledger → critic ACCEPT.
- Critic-driven retry: agent emits `done` with empty ledger → critic REJECT with gaps → orchestrator appends gap message → second engagement → critic ACCEPT.
- Critic exhaustion: critic REJECT for `max_critic_rejections + 1` times → returns last draft with `CRITIC_EXHAUSTED`.
- Preemption: budget cap hits mid-engagement → falls through to v1 cap-cut synthesis path.

End-to-end smoke (manual, not CI): one real scan with `--agent critic_react_v3 --preset coffee_career --goal "..."` against a known subject; assert tool-call count and engagement count are both higher than the same subject under `react_v1`.

### Risks and open questions

- **Grok 4.20 may not emit batched tool calls reliably.** If the model insists on sequential singletons even with prompt mandate, Discipline 1 collapses. Mitigation: an optional soft warning if the model emits a singleton when the ledger has 2+ open items would help — but rejected (option `c.i`). If empirical results show this is a problem, revisit. Worst case: this design degrades to "critic-driven react_v1" — still better than today, but the parallelism win is lost.
- **Critic leniency.** A lenient critic accepts shallow reports; a strict critic burns budget on cosmetic gaps. The critic prompt's "concrete identifier never followed up on" rule is the main enforcement lever; tune it after a few real scans.
- **Identifier extraction at done.** Reusing v1's `parse_report` requires the model to emit the JSON envelope at the end of the final assistant message. If a critic-rejected engagement re-runs and the new final message lacks the envelope, `parse_report` returns an empty identifier dict. The system prompt must restate the envelope contract on every critic re-engagement; the orchestrator's gap message should also remind the model.
- **Cost ceiling under repeated rejections.** Three rejections × ~50 tool calls + four critic calls is the worst-case cost. Budget enforcement is unchanged (whole-scan cap), so the floor is honored — but a user setting `max_critic_rejections=10` with `budget_usd=20` could spend a lot. Document this in the CLI help.
- **Preset drift.** Adding a seventh preset later requires updating `PRESETS`, `Literal` in `ScanConfig`, the CLI choices, and the README. Acceptable since preset count is small and bounded.
