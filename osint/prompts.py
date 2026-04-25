import json
import re


SYSTEM_TEMPLATE = """\
You are a self-OSINT agent. The user wants to know what is publicly
discoverable about themselves online. The subject is the caller; the caller
has consented.

SUBJECT DESCRIPTION:
---
{subject}
---

Steps:
1. Parse the description into a structured set of identifiers (emails, phones,
   usernames, full-name variants, schools, employers, cities, platform URLs).
2. Use the tools below to investigate. Call multiple tools in the same turn
   when queries are independent; prefer cheap/broad tools before paid/narrow.
3. Extract as much as possible from each tool response before spending more.
4. Stop calling tools when nothing new is likely to surface or when you
   believe you have enough evidence.

Available tools: {tool_names}

Routing guidance (use the right tool for the job, not whichever happens to
match the description first):
{routing_guidance}

When you are ready to finish, return ONLY a single assistant message with NO
tool calls, containing one fenced JSON block of this exact shape:

```json
{{
  "extracted_identifiers": {{ "emails": [...], "usernames": [...], "urls": [...] }},
  "report": {{
    "summary": "...",
    "accounts": [...],
    "web_presence": [...],
    "exposures": [...],
    "remediation": [...]
  }}
}}
```

The schema above is a guideline — add fields as needed. The fenced JSON is
what the user will read, so populate it fully.
"""


SYNTHESIS_TEMPLATE = """\
The scan was cut short. Reason: {stop_reason}.

Tool calls already made during this scan:
{tool_calls_summary}

Based on these results, produce the final report now. Return ONLY a fenced
JSON block with the shape:

```json
{{
  "extracted_identifiers": {{...}},
  "report": {{...}}
}}
```
"""


# Per-tool one-line routing rules, only included for tools actually enabled.
_ROUTING_RULES = {
    "tavily_search": "tavily_search — general web (news, blogs, personal sites, public profiles outside of X). The default for any open-web question.",
    "tavily_extract": "tavily_extract — read the full content of a specific URL you already have. Use after a search hit looks promising.",
    "maigret": "maigret — given a confirmed/likely username, map which sites that handle exists on. Don't use for general search; only when you have an actual username.",
    "apify_instagram": "apify_instagram — fetch a specific Instagram profile and recent posts. Requires a confirmed handle.",
    "apify_linkedin": "apify_linkedin — fetch a specific LinkedIn profile by full URL.",
    "apify_twitter": "apify_twitter — for ANY X (Twitter) content: pass `handle` to fetch a specific user's profile + recent tweets, or pass `search_query` to search tweets across X (e.g. for posts about the subject). Don't use tavily_search for X content; X's public surface is poorly indexed by general web search.",
}


def build_system_prompt(subject: str, tool_names: list[str]) -> str:
    rules = [f"- {_ROUTING_RULES[n]}" for n in tool_names if n in _ROUTING_RULES]
    routing_guidance = "\n".join(rules) if rules else "- (no enabled tools have specific routing rules)"
    return SYSTEM_TEMPLATE.format(
        subject=subject,
        tool_names=", ".join(tool_names),
        routing_guidance=routing_guidance,
    )


def build_synthesis_prompt(stop_reason: str, tool_calls_summary: str = "(no tool calls were made)") -> str:
    return SYNTHESIS_TEMPLATE.format(
        stop_reason=stop_reason,
        tool_calls_summary=tool_calls_summary,
    )


def format_tool_calls_for_synthesis(tool_calls: list, max_output_chars: int = 500) -> str:
    """One-line-per-call summary for the synthesis prompt.
    Format: `N. tool_name(input_dict) -> output_or_error[:max_chars]`
    Each output is JSON-serialized then truncated so a long-running scan's
    prompt stays bounded."""
    import json as _json
    if not tool_calls:
        return "(no tool calls were made)"
    lines = []
    for tc in tool_calls:
        inp = _json.dumps(tc.input, default=str, separators=(",", ":"))
        if tc.error:
            result = f"ERROR: {tc.error}"
        else:
            raw = _json.dumps(tc.output, default=str, separators=(",", ":")) if tc.output else "{}"
            result = raw[:max_output_chars] + ("…(truncated)" if len(raw) > max_output_chars else "")
        lines.append(f"{tc.turn}. {tc.tool}({inp}) → {result}")
    return "\n".join(lines)


_FENCED_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def parse_report(text: str) -> dict:
    text = text or ""
    candidates = []
    m = _FENCED_JSON.search(text)
    if m:
        candidates.append(m.group(1))
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    for c in candidates:
        try:
            data = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return {
                "extracted_identifiers": data.get("extracted_identifiers") or {},
                "report": data.get("report") or {},
            }
    return {"extracted_identifiers": {}, "report": {"text": text}}
