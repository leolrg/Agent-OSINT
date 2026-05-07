"""xAI Grok 4.20 multi-agent runner using Apify Remote MCP.

This runner intentionally bypasses LangGraph's Chat Completions-style
tool loop. xAI's multi-agent model is available through Responses API,
and Remote MCP tools execute server-side.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Callable

from langchain_core.language_models import BaseChatModel

from osint.agents.react_v1.prompts import parse_report
from osint.errors import ScanConfigError
from osint.state import ScanState, StopReason

XAI_MULTIAGENT_MODEL = "grok-4.20-multi-agent"
DEFAULT_REASONING_EFFORT = "low"
# xai_multiagent_v1 hardcodes the xAI Responses endpoint and key env var,
# ignoring LLMConfig.base_url / api_key_env_var. The model, prompt, and
# tool surfaces are all xAI-specific (Responses API, web_search, x_search,
# Apify MCP), so it must always hit xAI directly — even when the user has
# pointed the rest of the project at an OpenAI-compatible gateway via
# OSINT_LLM_BASE_URL. Other agents (react_v1, leadqueue_v2) honour the
# gateway override and remain swappable.
XAI_BASE_URL = "https://api.x.ai/v1"
XAI_API_KEY_ENV_VAR = "XAI_API_KEY"
# Approved Apify actors. Not used as a URL filter (see build_apify_mcp_url
# below); listed in the prompt so the model invokes them via Apify's
# `call-actor` meta-tool. Restrict the model to these three to avoid
# unbounded actor spend.
APIFY_MCP_ACTORS = (
    "dev_fusion/linkedin-profile-scraper",
    "apify/instagram-profile-scraper",
    "easyapi/all-in-one-rednote-xiaohongshu-scraper",
)


def build_apify_mcp_url(token: str | None = None) -> str:
    """Build the Apify Remote MCP URL.

    No `?tools=` filter: empirically xAI's Responses-API MCP client
    loses per-actor tool registrations between session init and tool
    call, surfacing every Apify tool invocation as "Tool not available".
    The bare URL exposes Apify's default 8 tools — including the
    generic `call-actor` meta-tool — which xAI registers reliably.
    The model invokes `call-actor` with `{actor, input}` to run any of
    APIFY_MCP_ACTORS, sidestepping the registration bug entirely.

    Token in URL: xAI's `authorization` field sends a bare
    `Authorization: <token>` header (no `Bearer ` prefix), which Apify
    rejects with 401 `invalid_token`. The `?token=` query param is
    Apify's officially supported alternative auth method.
    """
    url = "https://mcp.apify.com"
    if token:
        url += f"?token={token}"
    return url


def build_multiagent_prompt(subject: str) -> str:
    return f"""\
You are EliteOSINT, a world-class Open Source Intelligence (OSINT) analyst with over 20 years of experience from intelligence agencies, private investigation, and high-stakes corporate due diligence. You are obsessive, creative, systematic, and never satisfied with surface-level information. Your expertise is turning minimal seeds into extremely comprehensive human profiles using only publicly available sources.

When the user provides a person's name and any initial keywords (such as high school, university, company, city, industry, etc.), treat those keywords **only as initial seeds**. Do NOT limit your search to them. Your mission is to aggressively broaden the investigation in every possible direction, go many layers deep, and leave no digital stone unturned.

Core Rules:
- Always think iteratively: every new piece of information must generate 5–10 new search vectors (associates, family members, colleagues, former employers, schools, locations, time periods, username patterns, etc.).
- Actively generate and search name variations, nicknames, transliterations, old names, pinyin + Chinese characters (if applicable), maiden names, common misspellings, and abbreviations.
- Hunt for digital exhaust across ALL platforms and eras: current and historical social media (X/Twitter, LinkedIn, Facebook, Instagram, TikTok, Weibo, Xiaohongshu, Douyin, GitHub, forums, old blogs), academic databases (Google Scholar, ResearchGate, CNKI), news archives, Wayback Machine, public records, court documents, property records, patents, media interviews, photos, and leaked but publicly indexed data.
- Perform network analysis: identify family, romantic partners, close friends, key colleagues, bosses, and subordinates, then investigate them for additional leads on the primary target.
- Use advanced search thinking (exact phrases, site-specific operators, date ranges, filetype, before/after dates, etc.).
- Separate facts from inferences. Assign confidence levels (High/Medium/Low) to every major claim. Never hallucinate.
- If information seems deleted or hidden, note it and suggest possible pivots (e.g. old usernames, cached versions, associates who mentioned them).

Mandatory Search Dimensions (always explore these and expand beyond):
1. Full identity & name variations
2. Education history (all possible schools, classmates, alumni activity)
3. Complete professional/career timeline (every company, role, projects, publications, colleagues)
4. Digital footprint & usernames across all platforms (past and present)
5. Geographic history (past and current addresses, cities lived in, travel patterns)
6. Family and personal relationships
7. Media mentions, controversies, achievements, public statements
8. Academic, technical, or creative output
9. Any legal, financial, or regulatory footprints (if publicly available)
10. Behavioral patterns, interests, and online language signatures

Output Format (use this exact structure, be extremely detailed and verbose where valuable):

**Executive Summary**
**Identified Name Variations & Aliases**
**Comprehensive Profile** ( subsections: Personal Background, Education, Professional History, Geographic Footprint, etc.)
**Digital & Social Media Footprint** (list all accounts found, old usernames, linked emails/phones if public)
**Key Associates & Network Map** (explain relevance of each person)
**Timeline of Significant Events**
**Hypotheses, Patterns & Potential Red Flags** (with confidence levels)
**Leads for Further Investigation** (prioritized list of high-value next steps and specific searches)
**Sources** (list all major sources clearly)
**Overall Assessment** (depth of coverage, remaining blind spots, confidence in the profile)

Begin your investigation immediately upon receiving the target's name and any seeds. Show your reasoning process explicitly. Start by listing all the search strategies and name variations you will pursue before diving into findings. Be relentless — the goal is maximum depth.


Available research surfaces:
- Use xAI built-in `web_search` to discover candidate public LinkedIn,
  Instagram, RedNote/Xiaohongshu profiles or posts, personal sites, articles,
  profile snippets, and other public pages that help disambiguate identity.
- Use xAI built-in `x_search` to discover X/Twitter mentions that may reveal
  Instagram handles, LinkedIn URLs, name variants, organizations, geography,
  or profile cross-links.
- Use the Apify MCP `call-actor` tool to run social-media scrapers once you
  have candidate profile URLs, usernames, or keywords. The `call-actor` tool
  takes `{{"actor": "<id>", "input": {{...}}}}`. Only invoke these three
  approved actors:

  1. `dev_fusion/linkedin-profile-scraper` — LinkedIn profile scrape.
     input: `{{"profileUrls": ["https://www.linkedin.com/in/<slug>"]}}`

  2. `apify/instagram-profile-scraper` — Instagram profile + recent posts.
     input: `{{"usernames": ["<handle without @>"], "resultsLimit": 30}}`

  3. `easyapi/all-in-one-rednote-xiaohongshu-scraper` — RedNote/Xiaohongshu.
     input requires `mode` plus the matching field:
     - profile lookup: `{{"mode": "profile", "profileUrls": ["https://www.xiaohongshu.com/user/profile/<id>"], "maxItems": 30}}`
     - user posts:     `{{"mode": "userPosts", "profileUrls": ["..."], "maxItems": 30}}`
     - keyword search: `{{"mode": "search", "keywords": ["..."], "maxItems": 30}}`
     - post comments:  `{{"mode": "comment", "postUrls": ["..."], "maxItems": 30}}`

  Do NOT invoke `apify--rag-web-browser`, `search-actors`, or any actor not
  in the list above. Only call a scraper after `web_search`/`x_search` has
  given you a concrete URL/handle/keyword to feed it.

In Sources, cite the search/tool surface used for every major claim, e.g.
`web_search result`, `x_search result`, `call-actor dev_fusion/linkedin-profile-scraper`,
`call-actor apify/instagram-profile-scraper`, or
`call-actor easyapi/all-in-one-rednote-xiaohongshu-scraper`.

End with exactly one fenced JSON block containing only extracted identifiers:

```json
{{
  "extracted_identifiers": {{
    "emails": [],
    "usernames": [],
    "urls": [],
    "name_variations": [],
    "schools": [],
    "employers": [],
    "locations": []
  }}
}}
```

SUBJECT:
{subject}
"""


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _jsonish(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_jsonish(x) for x in obj]
    if isinstance(obj, tuple):
        return [_jsonish(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _jsonish(v) for k, v in obj.items()}
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json")
        except TypeError:
            return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return {k: _jsonish(v) for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)


def _usage_tokens(usage: Any) -> tuple[int, int, dict[str, Any]]:
    usage_dict = _jsonish(usage) or {}
    input_tokens = (
        _get_attr(usage, "input_tokens")
        or _get_attr(usage, "prompt_tokens")
        or usage_dict.get("input_tokens")
        or usage_dict.get("prompt_tokens")
        or 0
    )
    output_tokens = (
        _get_attr(usage, "output_tokens")
        or _get_attr(usage, "completion_tokens")
        or usage_dict.get("output_tokens")
        or usage_dict.get("completion_tokens")
        or 0
    )
    return int(input_tokens or 0), int(output_tokens or 0), usage_dict


def _response_text(response: Any) -> str:
    text = _get_attr(response, "output_text")
    if text:
        return str(text)

    # OpenAI Responses shape: output -> message -> content -> output_text.
    chunks: list[str] = []
    for item in _get_attr(response, "output", []) or []:
        if _get_attr(item, "type") != "message":
            continue
        for content in _get_attr(item, "content", []) or []:
            if _get_attr(content, "type") == "output_text":
                chunks.append(str(_get_attr(content, "text", "")))
    return "\n".join(c for c in chunks if c).strip()


def _mcp_items(response: Any) -> list[dict[str, Any]]:
    """Extract MCP-related items from Responses API output for diagnostics.

    Captures `mcp_list_tools`, `mcp_call`, and `mcp_approval_*` so we can
    see what the server returned, what args were sent, and what errors
    came back. Without this the runner is opaque when MCP fails.
    """
    items: list[dict[str, Any]] = []
    for item in _get_attr(response, "output", []) or []:
        item_type = _get_attr(item, "type") or ""
        if isinstance(item_type, str) and item_type.startswith("mcp"):
            items.append(_jsonish(item))
    return items


def _load_openai_client_factory() -> Callable[..., Any]:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ScanConfigError(
            "xai_multiagent_v1 requires the `openai` package. "
            "Install project dependencies from pyproject.toml."
        ) from e
    return OpenAI


class XaiMultiAgentV1Runner:
    """Responses API runner for xAI's hosted multi-agent model."""

    def __init__(self, client_factory: Callable[..., Any] | None = None):
        self._client_factory = client_factory

    async def run(
        self,
        *,
        subject: str,
        state: ScanState,
        llm: BaseChatModel,
        tools: list[Any],
        cost_cb: Any,
    ) -> tuple[dict, StopReason | None]:
        xai_key = os.environ.get(XAI_API_KEY_ENV_VAR)
        if not xai_key:
            raise ScanConfigError(
                f"{XAI_API_KEY_ENV_VAR} is not set "
                f"(required by {XAI_MULTIAGENT_MODEL})"
            )
        apify_token = os.environ.get("APIFY_TOKEN")
        if not apify_token:
            raise ScanConfigError(
                "APIFY_TOKEN is not set (required for Apify Remote MCP)"
            )

        client_factory = self._client_factory or _load_openai_client_factory()
        client = client_factory(
            api_key=xai_key,
            base_url=XAI_BASE_URL,
            timeout=state.config.max_wall_clock_sec,
        )
        reasoning_effort = (
            state.config.tool_options
            .get("xai_multiagent", {})
            .get("reasoning_effort", DEFAULT_REASONING_EFFORT)
        )

        prompt = build_multiagent_prompt(subject)
        request = {
            "model": XAI_MULTIAGENT_MODEL,
            "reasoning": {"effort": reasoning_effort},
            "input": [{"role": "user", "content": prompt}],
            "tools": [
                {"type": "web_search"},
                {"type": "x_search"},
                {
                    "type": "mcp",
                    "server_url": build_apify_mcp_url(token=apify_token),
                    "server_label": "apify",
                    "server_description": (
                        "Apify generic actor invocation. Use the "
                        "`call-actor` tool to run LinkedIn, Instagram, "
                        "and RedNote/Xiaohongshu scrapers."
                    ),
                }
            ],
        }

        response = await asyncio.to_thread(client.responses.create, **request)
        text = _response_text(response)
        parsed = parse_report(text)

        usage = _get_attr(response, "usage")
        input_tokens, output_tokens, usage_dict = _usage_tokens(usage)
        state.record_llm_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        report = dict(parsed.get("report") or {})
        report["_xai_model"] = XAI_MULTIAGENT_MODEL
        report["_xai_reasoning_effort"] = reasoning_effort
        report["_xai_usage"] = usage_dict
        # xAI nests server-side tool usage details under `usage`, not as a
        # sibling field on the response. Read from the parsed usage_dict.
        report["_xai_server_side_tool_usage"] = (
            usage_dict.get("server_side_tool_usage_details")
            if isinstance(usage_dict, dict)
            else None
        )
        report["_xai_mcp_items"] = _mcp_items(response)
        state.record_final_report(
            report,
            identifiers=parsed.get("extracted_identifiers") or {},
        )
        state.messages.extend([
            {"type": "human", "content": prompt},
            {
                "type": "ai",
                "content": text,
                "response_id": _get_attr(response, "id"),
                "model": _get_attr(response, "model", XAI_MULTIAGENT_MODEL),
            },
        ])
        state.pass_reports.append({
            "pass_num": 1,
            "report": report,
            "extracted_identifiers": parsed.get("extracted_identifiers") or {},
            "stop_reason": None,
        })
        return parsed, None
