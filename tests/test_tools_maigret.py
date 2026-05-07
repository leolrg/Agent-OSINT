from unittest.mock import AsyncMock

import pytest
from maigret.result import MaigretCheckResult, MaigretCheckStatus

from osint.tools.maigret import MaigretTool


def _fake_result(status: MaigretCheckStatus, site: str, url: str = "") -> MaigretCheckResult:
    return MaigretCheckResult(username="jdoe", site_name=site, site_url_user=url, status=status)


async def test_maigret_calls_search_with_defaults(mocker):
    fake_search = AsyncMock(return_value={
        "GitHub": {
            "status": _fake_result(MaigretCheckStatus.CLAIMED, "GitHub", "https://github.com/j"),
            "url_user": "https://github.com/j",
        },
        "NotFound": {
            "status": _fake_result(MaigretCheckStatus.AVAILABLE, "NotFound"),
            "url_user": "",
        },
    })
    mocker.patch("osint.tools.maigret._search", fake_search)
    tool = MaigretTool()
    content, artifact = await tool._arun(username="jdoe", max_connections=15, timeout=10)
    kwargs = fake_search.call_args.kwargs
    assert kwargs["username"] == "jdoe"
    assert kwargs["max_connections"] == 15
    assert kwargs["timeout"] == 10
    assert kwargs.get("proxy") is None
    assert "GitHub" in content
    assert artifact["found_accounts"][0]["site"] == "GitHub"
    assert "raw" in artifact


async def test_maigret_forwards_overrides(mocker):
    fake_search = AsyncMock(return_value={})
    mocker.patch("osint.tools.maigret._search", fake_search)
    tool = MaigretTool(proxy_url="http://p:8080")
    await tool._arun(username="jdoe", max_connections=5, timeout=10, sites_filter=["GitHub", "Reddit"])
    kwargs = fake_search.call_args.kwargs
    assert kwargs["max_connections"] == 5
    assert kwargs["proxy"] == "http://p:8080"
    assert kwargs["site_list"] == ["GitHub", "Reddit"]


async def test_maigret_metadata():
    tool = MaigretTool()
    assert tool.name == "maigret"
    assert tool.response_format == "content_and_artifact"


def test_maigret_real_api_compatibility():
    """Contract test: fails fast if installed maigret's `search` signature
    drifts away from what _search assumes. Catches the C1-class bug from
    Task 7's review without requiring a real network scrape."""
    import inspect
    import maigret as _m

    sig = inspect.signature(_m.search)
    params = sig.parameters
    # We rely on these parameter names (whatever subset _search uses).
    # Update this list and _search together if the API changes.
    expected = {"username", "site_dict", "logger", "timeout", "max_connections", "proxy"}
    missing = expected - set(params.keys())
    assert not missing, f"maigret.search missing expected params: {missing}"
    assert inspect.iscoroutinefunction(_m.search), "maigret.search must be async"
