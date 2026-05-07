import pytest

from osint.errors import ScanConfigError
from osint.state import ScanState
from osint.tools import build_tools
from osint.types import ScanConfig


@pytest.fixture(autouse=True)
def _clear_web_provider_env(monkeypatch):
    monkeypatch.delenv("OSINT_WEB_PROVIDER", raising=False)
    monkeypatch.delenv("OSINT_WEB_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("OSINT_WEB_EXTRACT_PROVIDER", raising=False)


def test_build_tools_without_apify_token_raises(monkeypatch):
    """Apify remains the default web provider, so missing APIFY_TOKEN must
    fail fast instead of constructing a tool that crashes on first call."""
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


def test_build_tools_can_select_different_search_and_extract_providers(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "apify-k")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-k")
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    cfg = ScanConfig(
        enabled_tools={"web_search", "web_extract"},
        tool_options={
            "web": {
                "search_provider": "tavily",
                "extract_provider": "apify",
            }
        },
    )

    tools = build_tools(cfg, state)

    costs_by_name = {t.name: t._est_cost_usd for t in tools}
    assert costs_by_name == {"web_search": 0.016, "web_extract": 0.001}


def test_build_tools_env_can_select_different_search_and_extract_providers(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "apify-k")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-k")
    monkeypatch.setenv("OSINT_WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("OSINT_WEB_EXTRACT_PROVIDER", "apify")
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())

    tools = build_tools(ScanConfig(enabled_tools={"web_search", "web_extract"}), state)

    costs_by_name = {t.name: t._est_cost_usd for t in tools}
    assert costs_by_name == {"web_search": 0.016, "web_extract": 0.001}


def test_build_tools_tavily_requires_tavily_api_key(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "k")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    cfg = ScanConfig(
        enabled_tools={"web_search"},
        tool_options={"web": {"search_provider": "tavily"}},
    )

    with pytest.raises(ScanConfigError, match="TAVILY_API_KEY"):
        build_tools(cfg, state)


def test_build_tools_ignores_shared_web_provider_env(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-k")
    monkeypatch.setenv("OSINT_WEB_PROVIDER", "tavily")
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())

    with pytest.raises(ScanConfigError, match="APIFY_TOKEN"):
        build_tools(ScanConfig(enabled_tools={"web_search"}), state)


def test_build_tools_rejects_shared_web_provider_option(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "apify-k")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-k")
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    cfg = ScanConfig(
        enabled_tools={"web_search"},
        tool_options={"web": {"provider": "tavily"}},
    )

    with pytest.raises(ScanConfigError, match="search_provider"):
        build_tools(cfg, state)


def test_build_tools_uses_tavily_web_costs(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-k")
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    cfg = ScanConfig(
        enabled_tools={"web_search", "web_extract"},
        tool_options={
            "web": {
                "search_provider": "tavily",
                "extract_provider": "tavily",
            }
        },
    )

    tools = build_tools(cfg, state)

    costs_by_name = {t.name: t._est_cost_usd for t in tools}
    assert costs_by_name == {"web_search": 0.016, "web_extract": 0.016}


def test_build_tools_paid_requires_apify_token(monkeypatch):
    """Apify-backed social tools still require APIFY_TOKEN."""
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
