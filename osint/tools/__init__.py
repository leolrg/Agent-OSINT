import os

from langchain_core.tools import BaseTool

from osint.capped_tool import CappedTool
from osint.errors import ScanConfigError
from osint.state import ScanState
from osint.tools.apify import (
    ApifyInstagramTool,
    ApifyLinkedInTool,
    ApifyTwitterTool,
)
from osint.tools.maigret import MaigretTool
from osint.tools.tavily import make_tavily_extract, make_tavily_search
from osint.types import ScanConfig


# Per-call cost estimates (USD). Sourced from each vendor's published pricing
# as of 2026-04. These feed scan budget enforcement; if you switch Apify
# actors or move Tavily plans, update them here.
_COSTS = {
    # Tavily basic search = 1 credit; PAYG = $0.008/credit.
    "tavily_search": 0.008,
    # Tavily extract = 1 credit per 5 URLs (basic). One call typically extracts
    # 1-5 URLs; budget the worst-case-cheap-tier at 1 credit.
    "tavily_extract": 0.008,
    # Local library; no vendor cost.
    "maigret": 0.0,
    # apify/instagram-scraper: $1.50 per 1,000 results. One call returns
    # 1 profile + N posts (default 20) = ~21 items × $0.0015 = ~$0.03.
    "apify_instagram": 0.03,
    # apify/linkedin-profile-scraper (dev_fusion): $10.00 per 1,000 profiles.
    # One call = 1 profile = $0.01.
    "apify_linkedin": 0.01,
    # apidojo/twitter-scraper-lite: ~$0.016 per standard query (covers up to
    # ~40 tweets). Round up for safety.
    "apify_twitter": 0.02,
}


def _require_env(var: str, tool: str) -> None:
    if not os.environ.get(var):
        raise ScanConfigError(f"{tool} enabled but {var} is not set")


def _make_raw_tool(name: str, config: ScanConfig) -> BaseTool:
    if name == "tavily_search":
        _require_env("TAVILY_API_KEY", name)
        return make_tavily_search()
    if name == "tavily_extract":
        _require_env("TAVILY_API_KEY", name)
        return make_tavily_extract()
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
