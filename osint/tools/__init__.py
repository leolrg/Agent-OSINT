import os

from langchain_core.tools import BaseTool

from osint.capped_tool import CappedTool
from osint.errors import ScanConfigError
from osint.state import ScanState
from osint.tools.apify import (
    ApifyInstagramTool,
    ApifyLinkedInTool,
    ApifyTwitterTool,
    WebExtractTool,
    WebSearchTool,
)
from osint.tools.maigret import MaigretTool
from osint.tools.tavily import TavilyExtractTool, TavilySearchTool
from osint.types import ScanConfig


# Per-call cost estimates (USD). Sourced from each vendor's published pricing
# as of 2026-04. These feed scan budget enforcement; update them here when
# you swap an Apify actor.
_COSTS = {
    # apify/google-search-scraper: $1.80 per 1,000 SERP pages. Default
    # max_results=30 -> 3 pages -> $0.0054. Each pages = 10 organic results
    # (Google's hard cap; the actor's resultsPerPage parameter is ignored).
    "web_search": 0.006,
    # apify/website-content-crawler in `cheerio` mode (HTTP-only, no
    # browser): ~$0.20 per 1,000 pages. Each call extracts 1-5 URLs;
    # budget conservatively at one HTTP page per call.
    "web_extract": 0.001,
    # Local library; no vendor cost.
    "maigret": 0.0,
    # apify/instagram-scraper: $1.50 per 1,000 results. One call returns
    # 1 profile + N posts (default 20) = ~21 items × $0.0015 = ~$0.03.
    "apify_instagram": 0.03,
    # apify/linkedin-profile-scraper (dev_fusion): $10.00 per 1,000 profiles.
    # One call = 1 profile = $0.01.
    "apify_linkedin": 0.01,
    # gentle_cloud/twitter-tweets-scraper: per-actor-compute pricing.
    # Empirically ~$0.04 per call on STARTER plan (cookie-rotation overhead);
    # rounded up for safety. Switched from apidojo~twitter-scraper-lite which
    # silently returns demo data ({"demo": true}) on our account: the actor
    # errors with "Access to this origin is disabled" but reports SUCCEEDED,
    # so the agent treats placeholder data as real. Verified 2026-04-26
    # against real @semona0x lookups.
    "apify_twitter": 0.04,
}


_TAVILY_WEB_COSTS = {
    # Tavily advanced search = 2 credits; PAYG = $0.008/credit.
    "web_search": 0.016,
    # Tavily advanced extract = 2 credits per 5 successful extractions; most
    # agent calls batch a small set of URLs, so budget one credit per call.
    "web_extract": 0.016,
}


def _require_env(var: str, tool: str) -> None:
    if not os.environ.get(var):
        raise ScanConfigError(f"{tool} enabled but {var} is not set")


def _validate_web_provider(provider: str) -> str:
    provider = provider.lower()
    if provider not in {"apify", "tavily"}:
        raise ScanConfigError(
            f"unsupported web provider: {provider!r}; expected 'apify' or 'tavily'"
        )
    return provider


def _web_provider(config: ScanConfig, name: str) -> str:
    opts = config.tool_options.get("web", {})
    if "provider" in opts:
        raise ScanConfigError(
            "tool_options.web.provider is no longer supported; use "
            "tool_options.web.search_provider and/or "
            "tool_options.web.extract_provider"
        )
    provider_key = "search_provider" if name == "web_search" else "extract_provider"
    env_var = "OSINT_WEB_SEARCH_PROVIDER" if name == "web_search" else "OSINT_WEB_EXTRACT_PROVIDER"
    provider = (
        opts.get(provider_key)
        or os.environ.get(env_var)
        or "apify"
    )
    return _validate_web_provider(str(provider))


def _make_web_tool(name: str, config: ScanConfig) -> BaseTool:
    provider = _web_provider(config, name)
    if provider == "tavily":
        _require_env("TAVILY_API_KEY", name)
        opts = config.tool_options.get("web", {})
        if name == "web_search":
            return TavilySearchTool()
        return TavilyExtractTool(extract_depth=opts.get("extract_depth", "advanced"))

    _require_env("APIFY_TOKEN", name)
    if name == "web_search":
        return WebSearchTool()
    return WebExtractTool()


def _make_raw_tool(name: str, config: ScanConfig) -> BaseTool:
    if name == "web_search":
        return _make_web_tool(name, config)
    if name == "web_extract":
        return _make_web_tool(name, config)
    if name == "maigret":
        opts = config.tool_options.get("maigret", {})
        return MaigretTool(proxy_url=opts.get("proxy_url"))
    if name == "apify_instagram":
        _require_env("APIFY_TOKEN", name)
        return ApifyInstagramTool()
    if name == "apify_linkedin":
        _require_env("APIFY_TOKEN", name)
        return ApifyLinkedInTool()
    if name == "apify_twitter":
        _require_env("APIFY_TOKEN", name)
        return ApifyTwitterTool()
    raise ScanConfigError(f"unknown tool: {name}")


def build_tools(config: ScanConfig, state: ScanState) -> list[CappedTool]:
    tools: list[CappedTool] = []
    for name in sorted(config.enabled_tools):
        raw = _make_raw_tool(name, config)
        est_cost = _COSTS.get(name, 0.0)
        if name in {"web_search", "web_extract"} and _web_provider(config, name) == "tavily":
            est_cost = _TAVILY_WEB_COSTS[name]
        tools.append(CappedTool(wrapped=raw, state=state, est_cost_usd=est_cost))
    return tools
