"""Prompt templates for the lead-queue agent's three LLM personas:
processor (per-lead investigator), synthesizer (final report writer),
verifier (gap-finder + lead-proposer)."""
from __future__ import annotations


PROCESSOR_SYSTEM = """\
You are processing ONE lead in an OSINT investigation. Your job is small
and focused — investigate ONLY this lead, then return findings + new
leads.

Inputs you'll see:
- Subject: the person being investigated
- Lead: a focused instruction (e.g. "investigate handle simonwen.eth")
- Findings so far: a compact bullet list of previously-confirmed facts
- Available tools: web_search, web_extract, apify_*, maigret (per scan
  config)

Output: a single JSON object, no prose:

```json
{
  "findings": [
    {
      "claim": "<natural-language fact>",
      "evidence": [
        {"tool_call_id": "<id>", "snippet_quote": "<exact text>"}
      ],
      "confidence": "high" | "medium" | "low",
      "tags": ["handle", "instagram"]
    }
  ],
  "new_leads": [
    {
      "kind": "investigate_url",
      "description": "<focused instruction for next investigator>",
      "priority": 1-100
    }
  ]
}
```

Rules:
- Run AT MOST 5 tool calls per lead. If a tool result is rich, prefer
  generating new_leads over making more tool calls yourself.
- Every claim MUST cite at least one tool call you ACTUALLY made in
  this turn. Do NOT cite findings from prior leads — those are already
  recorded.
- Do NOT include findings outside this lead's scope, but DO emit
  new_leads for things you noticed-but-didn't-investigate.
- Keep new_leads focused: one investigation per lead, not "do everything
  you can find". Ten focused leads beat one mega-lead.
"""


SYNTHESIZER_SYSTEM = """\
You are writing the final OSINT report from a complete findings record.
Every claim in the report MUST be grounded in a Finding from the input.

You receive TWO inputs: FINDINGS (structured) and TOOL_CALLS (raw).
FINDINGS are your primary source.

Findings format: a list of {claim, evidence: [{tool_call_id, snippet_quote}],
confidence, tags}.

You also receive TOOL_CALLS — a compact log of every tool call's raw
output. Use this to recover information the processor may have missed:

- INLINE HANDLE REVEALS: snippets sometimes contain explicit identity
  signals (`xhs/twitter:<handle>`, `@<x>.eth`, `<distinctive_email>@gmail.com`,
  Discord/Telegram handles). If you spot one that plausibly matches the
  subject, promote it to the **Digital & Social Media Footprint** section,
  cite the tool_call_id, quote the snippet verbatim, and mark confidence
  as "medium" if the processor didn't already record it.
- DISTINCTIVE EMAILS: an email like "antiemoxiaozhushou@gmail.com" is
  the subject's even when the processor abstracted it as "the org's
  contact email" — promote the literal address.
- Do NOT fabricate. If a snippet's reveal doesn't plausibly match the
  subject, leave it out.

Output the same prose-plus-tail-JSON format the previous OSINT system
used:

  1. Full prose report with these sections:
       **Executive Summary**
       **Identified Name Variations & Aliases**
       **Comprehensive Profile** (Personal Background, Education,
                                  Professional History, Geographic
                                  Footprint, etc.)
       **Digital & Social Media Footprint**
       **Key Associates & Network Map**
       **Timeline of Significant Events**
       **Hypotheses, Patterns & Potential Red Flags** (with confidence)
       **Leads for Further Investigation**
       **Sources** — for EVERY major claim, cite the tool call inline
                    (use the tool_call_id from the evidence).
       **Overall Assessment**

  2. Then ONE fenced JSON block at the very end:

```json
{
  "extracted_identifiers": {
    "emails": [...], "usernames": [...], "urls": [...],
    "name_variations": [...], "schools": [...], "employers": [...],
    "phones": [...], "addresses": [...]
  }
}
```

Rules:
- If a finding has confidence=low, mark it explicitly in the prose
  (e.g. "(low confidence)").
- Do NOT make up sources. Every cited tool_call_id must come from the
  findings list.
- Group findings by tag where the report sections suggest it (e.g.
  handle-tagged findings → Digital & Social Media Footprint).
"""


VERIFIER_SYSTEM = """\
You are auditing an OSINT report for coverage and grounding. You read
the draft report, the full findings list, and the list of leads
already processed. Return one of:

  - {"satisfied": true, "gaps": [], "new_leads": []} — accept the report
  - {"satisfied": false, "gaps": [...], "new_leads": [...]} — request
    more investigation

When to mark UN-satisfied:
- A claim in the report has no matching finding (ungrounded). List it
  as a gap; ALSO emit a new_lead with description = "verify the claim
  '<text>' or remove it from the report".
- An obvious dimension is missing. Examples: report mentions employer
  but no LinkedIn evidence; subject is technical but no GitHub probe
  done; subject is Chinese but no zhihu/weibo/xiaohongshu searches
  (note: apify_xiaohongshu searches RedNote directly when the keyword
  has plausible CN-platform presence); report mentions a project name
  without sources. List as gap; emit a new_lead.

When to mark satisfied:
- Every report claim is grounded.
- The investigation has addressed each Mandatory Search Dimension that
  has plausible signal for this subject.
- You've already proposed leads on this dimension in a prior verifier
  iteration AND they were processed (check the leads_log).

Output one JSON object, no prose. Keep new_leads:
- focused (one investigation per lead)
- different from leads in leads_log (the queue dedups but the LLM
  shouldn't waste a slot on a duplicate)
- prioritized 80–100 (verifier-proposed leads should jump the queue)
"""


def format_findings_compact(findings: list, max_chars: int = 6000) -> str:
    """One-line-per-finding summary fed to processor + verifier prompts.

    Truncated at max_chars so the running findings record can't blow
    the LLM's context window.
    """
    lines = []
    for i, f in enumerate(findings):
        # f is a Finding model; render claim + first-evidence snippet
        ev = f.evidence[0] if f.evidence else None
        evstr = f"[{ev.tool_call_id}] {ev.snippet_quote[:80]}" if ev else "(no evidence)"
        line = f"{i+1}. ({f.confidence}) {f.claim}  ← {evstr}"
        lines.append(line)
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n…(truncated)"
    return out


def format_tool_calls_compact(tool_calls, max_chars: int = 15000) -> str:
    """Compact view of every tool call's key surface, for the synthesizer
    to mine for inline handle reveals the processor may have missed.

    Format per call:
        [<tool_call_id>] <tool>(<input_summary>)
          - <url> | <title> | <snippet 0..200 chars>
          - <url> | <title> | <snippet 0..200 chars>
        ...

    For tavily_extract / web_extract calls (raw_content), shows the URL
    plus the first ~600 chars of raw_content. Truncates the whole block
    at max_chars; appends "…(truncated)" marker.
    """
    def _get(tc, k, default=None):
        if hasattr(tc, k):
            return getattr(tc, k, default)
        if isinstance(tc, dict):
            return tc.get(k, default)
        return default

    lines: list[str] = []
    for tc in tool_calls:
        tool = _get(tc, "tool") or "?"
        tcid = _get(tc, "tool_call_id") or "?"
        inp = _get(tc, "input") or {}
        out = _get(tc, "output") or {}

        # One-line input summary.
        inp_str = ""
        if isinstance(inp, dict):
            if "query" in inp:
                q = inp.get("query") or ""
                inp_str = f"q={str(q)[:80]!r}"
            elif "urls" in inp:
                urls = inp.get("urls") or []
                inp_str = f"urls={len(urls)}"
            else:
                inp_str = ", ".join(
                    f"{k}={str(v)[:40]}"
                    for k, v in inp.items()
                    if k not in ("include_domains", "exclude_domains", "include_images")
                )[:120]
        lines.append(f"[{tcid}] {tool}({inp_str})")

        # Render output: search results, then extract raw_content fallback.
        if isinstance(out, dict):
            results = out.get("results") or []
            search_rendered = False
            for r in results[:5]:
                if not isinstance(r, dict):
                    continue
                url = r.get("url") or "?"
                title = r.get("title") or ""
                snip = r.get("content") or ""
                raw = r.get("raw_content") or ""
                if snip or title:
                    lines.append(f"  - {url} | {str(title)[:80]} | {str(snip)[:200]}")
                    search_rendered = True
                elif raw:
                    # web_extract-style: URL + first ~600 chars of raw_content.
                    lines.append(f"  - {url} | raw: {str(raw)[:600]}")
                    search_rendered = True
            if not search_rendered and isinstance(out.get("items"), list):
                # Apify Twitter / Instagram / Maigret all return {"items": [...]}.
                # Render up to 5 items using a heuristic field-priority so each
                # source family (profiles, posts, per-site results) shows
                # whatever it has.
                items = out["items"]
                for it in items[:5]:
                    if not isinstance(it, dict):
                        lines.append(f"  - {str(it)[:200]}")
                        continue
                    bits: list[str] = []
                    # Profile-shaped fields first.
                    for field in ("username", "fullName", "name", "screen_name", "handle"):
                        v = it.get(field)
                        if v:
                            bits.append(
                                f"@{v}"
                                if field in ("username", "screen_name", "handle")
                                else str(v)
                            )
                            break
                    # Bio-shaped fields next.
                    for field in ("biography", "bio", "description"):
                        v = it.get(field)
                        if v:
                            bits.append(str(v)[:200])
                            break
                    # Content-shaped fields (tweet/post text) last.
                    for field in ("text", "full_text", "tweet", "raw_content", "title"):
                        v = it.get(field)
                        if v:
                            bits.append(str(v)[:300])
                            break
                    url = it.get("url") or it.get("permalink") or it.get("link") or ""
                    if url:
                        bits.insert(0, str(url))
                    lines.append("  - " + " | ".join(bits) if bits else f"  - {str(it)[:200]}")
                if len(items) > 5:
                    lines.append(f"  …and {len(items) - 5} more items (truncated)")
            elif not search_rendered:
                # Generic fallback for outputs without a results[] or items[]
                # structure. Compact JSON-ish dump.
                lines.append(f"  (output: {str(out)[:300]})")

    out_str = "\n".join(lines)
    if len(out_str) > max_chars:
        out_str = out_str[:max_chars] + "\n…(truncated)"
    return out_str


def format_leads_log_compact(leads_log: list, max_chars: int = 2000) -> str:
    """One-line-per-lead summary of already-processed leads, fed to verifier."""
    lines = [f"{i+1}. ({lead.kind}, p={lead.priority}) {lead.description}"
             for i, lead in enumerate(leads_log)]
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n…(truncated)"
    return out
