import pytest

from osint.errors import ScanConfigError
from osint.state import ScanState
from osint.tools import build_tools
from osint.types import ScanConfig


def test_build_tools_without_apify_token_raises(monkeypatch):
    """web_search and web_extract are Apify-backed (Google Search Scraper +
    Website Content Crawler). Missing APIFY_TOKEN must fail fast at build
    time instead of constructing a tool that would crash on first call."""
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    with pytest.raises(ScanConfigError):
        build_tools(ScanConfig(), state)


def test_build_tools_with_apify_token(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "k")
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    tools = build_tools(ScanConfig(enabled_tools={"web_search", "maigret"}), state)
    names = sorted(t.name for t in tools)
    assert names == ["maigret", "web_search"]


def test_build_tools_paid_requires_apify_token(monkeypatch):
    """All paid tool slots (web_search, web_extract, apify_*) need
    APIFY_TOKEN now that everything is Apify-backed."""
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    cfg = ScanConfig(enabled_tools={"web_search", "apify_instagram"})
    with pytest.raises(ScanConfigError):
        build_tools(cfg, state)


def test_build_tools_rejects_unknown_name(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "k")
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    with pytest.raises(ScanConfigError):
        build_tools(ScanConfig(enabled_tools={"nope"}), state)
