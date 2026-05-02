"""Prompt builders, preset library, and ledger parser for critic_react_v3.

Presets are canned `goal` preambles. The user's free-form `goal` (if any)
is appended after the preset preamble in the system prompt.

PRESET_HINTS is a one-line summary of each preset, used by the critic
prompt — the critic doesn't need the full preamble, just enough to know
what kind of investigation it's evaluating.
"""
from __future__ import annotations


PRESETS: dict[str, str] = {
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


PRESET_HINTS: dict[str, str] = {
    "coffee_career": "career-focused coffee chat: current role, recent work, talking points, things to avoid.",
    "coffee_personal": "personal coffee chat: hobbies, communities, recent posts, no invasive details.",
    "reconnect": "reconnect with old contact: recent moves, life events, conversation openers.",
    "sales_outreach": "warm outreach: company, role, recent public comms, mutual connections.",
    "dossier": "comprehensive dossier: identity, career, education, footprint, network, history.",
    "general": "free-form investigation guided by the user's goal text.",
}


_SYSTEM_TEMPLATE = """\
You are EliteOSINT, an open-source intelligence analyst. Investigate the
following SUBJECT to satisfy the GOAL using the available tools.

Treat those keywords only as initial seeds, not as
boundaries. Your job is to broaden the investigation, follow concrete
identifiers, and keep digging until the remaining unknowns are either
answered or explicitly dropped with a reason.

SUBJECT:
{subject}

GOAL:
{goal_block}

AVAILABLE TOOLS:
{tools_block}

RULES OF ENGAGEMENT:

1. PARALLELISM. When two or more tool calls are independent (do not depend
   on each other's output), emit them as a single batch in one assistant
   message. Sequential single calls when batching is possible is a defect.
   Examples of independent calls: searching multiple distinct queries;
   fetching multiple URLs; probing a handle on different platforms.

2. OPEN-QUESTION LEDGER. Begin every assistant message with a fenced JSON
   block of this exact shape:
   ```json
   {{"open": [], "answered": [], "dropped": []}}
   ```
   - `open`: free-form questions you still need to answer.
   - `answered`: questions you've answered, each with a brief evidence pointer.
   - `dropped`: questions you've decided not to pursue, with a brief reason.
   You MAY NOT terminate while `open` is non-empty.

3. STOP DISCIPLINE. Never stop if a finding contains a concrete identifier
   (email, handle, URL, platform user id) that has not been followed up on.
   Such an identifier is an unanswered open question by definition.

4. DEPTH DISCIPLINE. Always generate and search name variations, nicknames,
   transliterations, old names, common misspellings, handles, schools,
   employers, locations, and project names. Every new piece of information must generate
   5-10 new search vectors unless it is clearly irrelevant.
   If a search is weak or empty, try different phrasing, narrower context,
   date terms, platform names, and quoted variants before dropping that
   thread.

5. SEARCH AND EXTRACT DISCIPLINE. Search snippets are leads, not sufficient
   evidence for important claims. Before producing the final report, check
   whether you have tried at least 15 distinct web_search queries and at least 5 web_extract calls
   when those tools are enabled and budget allows.
   If not, keep investigating unless the open-question ledger explains why
   those calls are impossible or irrelevant.

6. IDENTITY VERIFICATION. Before treating any LinkedIn, Instagram, X,
   GitHub, personal site, or similar profile as the subject's, list at least
   2 cross-reference points that match the subject seeds (school, employer,
   geography, language/name variant, time period, project, mutual link).
   If fewer than 2 fields match, mark it uncertain and search again with
   narrower disambiguators.

7. FINAL REPORT. When (and only when) `open` is empty, emit your final
   report as prose using this exact structure, with citations to the tool
   calls that support major claims:

   **Executive Summary**
   **Identified Name Variations & Aliases**
   **Comprehensive Profile** (subsections such as Personal Background,
   Education, Professional History, Geographic Footprint, etc. as relevant)
   **Digital & Social Media Footprint**
   **Key Associates & Network Map**
   **Timeline of Significant Events**
   **Hypotheses, Patterns & Potential Red Flags** (with confidence levels)
   **Leads for Further Investigation**
   **Sources**
   **Overall Assessment**

   The prose IS the report; do not wrap the report itself in JSON or code
   fences. After the prose report, append EXACTLY ONE fenced JSON block keyed
   `extracted_identifiers` with this shape:
   ```json
   {{
     "extracted_identifiers": {{
       "emails": [],
       "usernames": [],
       "urls": [],
       "name_variations": [],
       "schools": [],
       "employers": [],
       "phones": [],
       "addresses": []
     }}
   }}
   ```

Use search syntax in web_search (quoted phrases, OR, site:, intitle:,
filetype:, when supported). Read every snippet word-for-word — handles, emails, and project
names commonly leak inline. Cite tool calls in your prose.
"""


def build_system_prompt(
    *,
    subject: str,
    goal: str,
    preset: str,
    tool_names: list[str],
) -> str:
    """Build the system prompt for one engagement.

    The preset preamble and the user-supplied goal are concatenated in
    that order under GOAL. Either may be empty; if both are, GOAL is
    just the preset's preamble. `preset` must be a key of `PRESETS` —
    callers should validate (Pydantic Literal already does at config time).
    """
    preamble = PRESETS.get(preset, PRESETS["general"])
    goal_block = preamble if not goal.strip() else f"{preamble}\n\nUser-specific goal: {goal.strip()}"
    tools_block = "\n".join(f"- {n}" for n in tool_names) if tool_names else "- (no tools enabled)"
    return _SYSTEM_TEMPLATE.format(
        subject=subject,
        goal_block=goal_block,
        tools_block=tools_block,
    )


import json
import re
from dataclasses import dataclass, field


_FENCED_JSON_FIRST = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class Ledger:
    """Open-question ledger parsed from an assistant message.

    Empty lists everywhere when no parsable ledger is present — callers
    treat that case as "no open questions" so a malformed ledger never
    blocks termination.
    """
    open: list[str] = field(default_factory=list)
    answered: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def parse_ledger(text: str) -> Ledger:
    """Parse the first fenced ```json``` block at the head of `text`.

    Returns an empty Ledger if no block is present or the block is
    malformed JSON or not an object.
    """
    if not text:
        return Ledger()
    m = _FENCED_JSON_FIRST.search(text)
    if not m:
        return Ledger()
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return Ledger()
    if not isinstance(data, dict):
        return Ledger()
    def _list(key: str) -> list[str]:
        v = data.get(key) or []
        return [str(x) for x in v] if isinstance(v, list) else []
    return Ledger(open=_list("open"), answered=_list("answered"), dropped=_list("dropped"))
