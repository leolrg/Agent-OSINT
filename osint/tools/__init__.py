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


def _require_env(var: str, tool: str) -> None:
    if not os.environ.get(var):
        raise ScanConfigError(f"{tool} enabled but {var} is not set")


def _make_raw_tool(name: str, config: ScanConfig) -> BaseTool:
    if name == "web_search":
        _require_env("APIFY_TOKEN", name)
        return WebSearchTool()
    if name == "web_extract":
        _require_env("APIFY_TOKEN", name)
        return WebExtractTool()
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
        tools.append(CappedTool(wrapped=raw, state=state, est_cost_usd=_COSTS.get(name, 0.0)))
    return tools
