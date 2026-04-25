import pytest

from osint.errors import ScanConfigError
from osint.state import ScanState
from osint.tools import build_tools
from osint.types import ScanConfig


def test_build_tools_free_tier_without_keys_raises(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    with pytest.raises(ScanConfigError):
        build_tools(ScanConfig(), state)


def test_build_tools_with_keys(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    tools = build_tools(ScanConfig(enabled_tools={"tavily_search", "maigret"}), state)
    names = sorted(t.name for t in tools)
    assert names == ["maigret", "tavily_search"]


def test_build_tools_paid_requires_their_keys(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    cfg = ScanConfig(enabled_tools={"tavily_search", "apify_instagram"})
    with pytest.raises(ScanConfigError):
        build_tools(cfg, state)


def test_build_tools_rejects_unknown_name(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    with pytest.raises(ScanConfigError):
        build_tools(ScanConfig(enabled_tools={"nope"}), state)
