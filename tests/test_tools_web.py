"""Tests for web_search / web_extract — Apify-backed Google search and
website content crawler. Tools live in osint/tools/apify.py."""
from unittest.mock import AsyncMock, MagicMock

from osint.tools.apify import WebExtractTool, WebSearchTool


def _fake_client(items):
    """Mock chain matching apify-client's actor.call surface (returns the
    raw API JSON; dataset id key is camelCase `defaultDatasetId`)."""
    client = MagicMock()
    actor = MagicMock()
    dataset = MagicMock()
    client.actor = MagicMock(return_value=actor)
    client.dataset = MagicMock(return_value=dataset)
    actor.call = AsyncMock(return_value={"defaultDatasetId": "ds1"})
    dataset.list_items = AsyncMock(return_value=MagicMock(items=items))
    return client, actor, dataset


async def test_web_search_pages_for_max_results():
    """max_results=30 must request 3 SERP pages. Google returns 10/page;
    the actor's resultsPerPage parameter is ignored (verified live 2026-04
    against apify/google-search-scraper)."""
    page = {"organicResults": [
        {"url": f"https://x{i}.com", "title": f"t{i}",
         "description": f"d{i}", "position": i}
        for i in range(10)
    ]}
    client, actor, _ = _fake_client([page, page, page])
    tool = WebSearchTool(client=client)
    content, artifact = await tool._arun(query='"Simon Wen" Guangzhou', max_results=30)
    run_input = actor.call.call_args.kwargs["run_input"]
    assert run_input["queries"] == '"Simon Wen" Guangzhou'
    assert run_input["maxPagesPerQuery"] == 3   # ceil(30/10)
    assert "saveHtml" in run_input and run_input["saveHtml"] is False
    # 3 pages × 10 organic results = 30 results, flattened in rank order.
    assert len(artifact["results"]) == 30
    assert artifact["results"][0]["url"] == "https://x0.com"


async def test_web_search_caps_at_max_results():
    """If the actor returns more results than asked for, output is truncated."""
    page = {"organicResults": [
        {"url": f"https://x{i}.com", "title": "t", "description": "d", "position": i}
        for i in range(10)
    ]}
    client, _, _ = _fake_client([page, page])  # 20 results returned
    tool = WebSearchTool(client=client)
    _, artifact = await tool._arun(query="q", max_results=15)
    assert len(artifact["results"]) == 15


async def test_web_search_metadata():
    """Tool registers as `web_search` with content_and_artifact format."""
    tool = WebSearchTool(client=MagicMock())
    assert tool.name == "web_search"
    assert tool.response_format == "content_and_artifact"


async def test_web_extract_passes_urls_in_apify_shape():
    """website-content-crawler takes startUrls=[{"url": ...}] (NOT a flat
    list) and we must set maxCrawlDepth=0 to disable link-following — without
    it the crawler walks the site and returns dozens of unrelated pages.
    Verified live 2026-04."""
    client, actor, _ = _fake_client([
        {"url": "https://example.com", "markdown": "# hi", "text": "hi"}
    ])
    tool = WebExtractTool(client=client)
    content, artifact = await tool._arun(urls=["https://example.com"])
    run_input = actor.call.call_args.kwargs["run_input"]
    assert run_input["startUrls"] == [{"url": "https://example.com"}]
    assert run_input["maxCrawlDepth"] == 0
    assert run_input["crawlerType"] == "cheerio"
    assert run_input["saveMarkdown"] is True
    assert artifact["results"][0]["raw_content"] == "# hi"


async def test_web_extract_metadata():
    tool = WebExtractTool(client=MagicMock())
    assert tool.name == "web_extract"
    assert tool.response_format == "content_and_artifact"
