import os

import pytest

from osint.tools.tavily import make_tavily_search, make_tavily_extract


def test_tavily_tools_names(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    search = make_tavily_search()
    extract = make_tavily_extract()
    assert search.name == "tavily_search"
    assert extract.name == "tavily_extract"


def test_tavily_search_requires_api_key(monkeypatch):
    from osint.errors import ScanConfigError
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(ScanConfigError):
        make_tavily_search()
