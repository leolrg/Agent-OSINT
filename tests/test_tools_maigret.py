from unittest.mock import AsyncMock

import pytest

from osint.tools.maigret import MaigretTool


async def test_maigret_calls_search_with_defaults(mocker):
    fake_search = AsyncMock(return_value={
        "GitHub": {"status": {"message": "Claimed"}, "url_user": "https://github.com/j"},
        "NotFound": {"status": {"message": "Available"}, "url_user": ""},
    })
    mocker.patch("osint.tools.maigret._search", fake_search)
    tool = MaigretTool()
    content, artifact = await tool._arun(username="jdoe")
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
    await tool._arun(username="jdoe", max_connections=5, sites_filter=["GitHub", "Reddit"])
    kwargs = fake_search.call_args.kwargs
    assert kwargs["max_connections"] == 5
    assert kwargs["proxy"] == "http://p:8080"
    assert kwargs["site_list"] == ["GitHub", "Reddit"]


async def test_maigret_metadata():
    tool = MaigretTool()
    assert tool.name == "maigret"
    assert tool.response_format == "content_and_artifact"
