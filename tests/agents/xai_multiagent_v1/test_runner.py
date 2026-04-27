import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from osint.agents.xai_multiagent_v1.runner import (
    APIFY_MCP_ACTORS,
    XaiMultiAgentV1Runner,
    build_apify_mcp_url,
    build_multiagent_prompt,
)
from osint.state import ScanState
from osint.types import ScanConfig


class _Responses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _Client:
    def __init__(self, response):
        self.responses = _Responses(response)


def _response(text="Report text", output=None, usage=None):
    return SimpleNamespace(
        output_text=text,
        usage=usage if usage is not None else SimpleNamespace(
            input_tokens=1000,
            output_tokens=2000,
            server_side_tool_usage_details={"mcp_calls": 2},
        ),
        output=output if output is not None else [],
        model="grok-4.20-multi-agent",
        id="resp_123",
    )


def test_build_apify_mcp_url_uses_bare_endpoint_with_token_only():
    """No `?tools=` filter — model invokes scrapers via call-actor.
    URL filter caused xAI to lose per-actor tool registration and
    surface every call as 'Tool not available'.
    """
    assert APIFY_MCP_ACTORS == (
        "dev_fusion/linkedin-profile-scraper",
        "apify/instagram-profile-scraper",
        "easyapi/all-in-one-rednote-xiaohongshu-scraper",
    )
    assert build_apify_mcp_url() == "https://mcp.apify.com"
    assert (
        build_apify_mcp_url(token="apify-key")
        == "https://mcp.apify.com?token=apify-key"
    )
    # Critically, no `tools=` filter must appear in the URL.
    assert "tools=" not in build_apify_mcp_url(token="apify-key")


def test_build_multiagent_prompt_uses_builtin_search_then_apify_profile_scrapers():
    prompt = build_multiagent_prompt("Jane Doe, NYU, @janed")

    assert "SUBJECT:\nJane Doe, NYU, @janed" in prompt
    assert "web_search" in prompt
    assert "x_search" in prompt
    # The prompt must teach `call-actor` since per-actor tool names are
    # registered unreliably by xAI's Responses-API MCP client.
    assert "call-actor" in prompt
    # All three approved actor IDs must appear so the model can pass
    # them to call-actor's `actor` argument.
    for actor in APIFY_MCP_ACTORS:
        assert actor in prompt
    # Input-shape hints for each actor must be present.
    assert "profileUrls" in prompt    # LinkedIn + RedNote
    assert "usernames" in prompt      # Instagram
    assert '"mode"' in prompt         # RedNote requires mode
    assert "RedNote" in prompt
    assert "Xiaohongshu" in prompt
    assert "extracted_identifiers" in prompt


@pytest.mark.asyncio
async def test_runner_calls_xai_responses_with_apify_mcp_only(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-key")
    monkeypatch.setenv("APIFY_TOKEN", "apify-key")
    client = _Client(_response("Found Jane on LinkedIn and Instagram."))
    runner = XaiMultiAgentV1Runner(client_factory=lambda **_: client)
    state = ScanState(scan_id="sid", subject="Jane Doe", config=ScanConfig())

    parsed, stop_reason = await runner.run(
        subject="Jane Doe",
        state=state,
        llm=MagicMock(),
        tools=[],
        cost_cb=MagicMock(),
    )

    assert stop_reason is None
    assert parsed["report"]["text"] == "Found Jane on LinkedIn and Instagram."
    assert state.report["text"] == "Found Jane on LinkedIn and Instagram."
    call = client.responses.calls[0]
    assert call["model"] == "grok-4.20-multi-agent"
    assert call["reasoning"] == {"effort": "low"}
    mcp_tool = call["tools"][2]
    assert mcp_tool["type"] == "mcp"
    assert mcp_tool["server_url"] == build_apify_mcp_url(token="apify-key")
    assert mcp_tool["server_label"] == "apify"
    assert "call-actor" in mcp_tool["server_description"]
    # The token must be in the URL — Apify requires the `Bearer ` prefix
    # on the Authorization header, which xAI's `authorization` field
    # does not provide.
    assert "token=apify-key" in mcp_tool["server_url"]
    assert "authorization" not in mcp_tool
    # No URL filter, no allowed_tools — both empirically broke the
    # per-actor registration. Model uses `call-actor` instead.
    assert "tools=" not in mcp_tool["server_url"]
    assert "allowed_tools" not in mcp_tool
    prompt = call["input"][0]["content"]
    assert "web_search" in prompt
    assert "x_search" in prompt
    assert "Apify MCP" in prompt


@pytest.mark.asyncio
async def test_runner_records_response_usage_for_multiagent_cost(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-key")
    monkeypatch.setenv("APIFY_TOKEN", "apify-key")
    client = _Client(_response("Report"))
    runner = XaiMultiAgentV1Runner(client_factory=lambda **_: client)
    state = ScanState(scan_id="sid", subject="Jane", config=ScanConfig())

    await runner.run(
        subject="Jane",
        state=state,
        llm=MagicMock(),
        tools=[],
        cost_cb=MagicMock(),
    )

    assert state.llm_input_tokens == 1000
    assert state.llm_output_tokens == 2000
    assert state.llm_cost_usd == pytest.approx(0.014)
    assert state.report["_xai_usage"] == {
        "input_tokens": 1000,
        "output_tokens": 2000,
        "server_side_tool_usage_details": {"mcp_calls": 2},
    }
    assert state.report["_xai_server_side_tool_usage"] == {"mcp_calls": 2}
    assert state.report["_xai_mcp_items"] == []


@pytest.mark.asyncio
async def test_runner_captures_mcp_output_items_for_diagnostics(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-key")
    monkeypatch.setenv("APIFY_TOKEN", "apify-key")
    output = [
        SimpleNamespace(
            type="mcp_list_tools",
            server_label="apify",
            tools=[{"name": "dev_fusion/linkedin-profile-scraper"}],
        ),
        SimpleNamespace(
            type="mcp_call",
            server_label="apify",
            name="dev_fusion/linkedin-profile-scraper",
            arguments='{"profileUrls":["https://linkedin.com/in/jane"]}',
            output="error: unauthorized",
            error="unauthorized",
        ),
        SimpleNamespace(type="message", content=[]),
    ]
    client = _Client(_response("Report", output=output))
    runner = XaiMultiAgentV1Runner(client_factory=lambda **_: client)
    state = ScanState(scan_id="sid", subject="Jane", config=ScanConfig())

    await runner.run(
        subject="Jane",
        state=state,
        llm=MagicMock(),
        tools=[],
        cost_cb=MagicMock(),
    )

    items = state.report["_xai_mcp_items"]
    assert [i["type"] for i in items] == ["mcp_list_tools", "mcp_call"]
    assert items[1]["name"] == "dev_fusion/linkedin-profile-scraper"
    assert items[1]["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_runner_pins_xai_endpoint_ignoring_llmconfig_overrides(monkeypatch):
    """xai_multiagent_v1 must hit api.x.ai directly, ignoring the project's
    OpenAI-compatible gateway override. Other agents stay swappable.
    """
    monkeypatch.setenv("XAI_API_KEY", "xai-key")
    monkeypatch.setenv("APIFY_TOKEN", "apify-key")
    monkeypatch.setenv("WRONG_GATEWAY_KEY", "should-not-be-used")
    captured: dict = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return _Client(_response("Report"))

    runner = XaiMultiAgentV1Runner(client_factory=factory)
    # Simulate a project-wide gateway override (Console Service AI etc.).
    config = ScanConfig(
        llm={
            "model": "gpt-5.5",
            "base_url": "https://gateway.example.com/v1",
            "api_key_env_var": "WRONG_GATEWAY_KEY",
        },
    )
    state = ScanState(scan_id="sid", subject="Jane", config=config)

    await runner.run(
        subject="Jane",
        state=state,
        llm=MagicMock(),
        tools=[],
        cost_cb=MagicMock(),
    )

    assert captured["base_url"] == "https://api.x.ai/v1"
    assert captured["api_key"] == "xai-key"


@pytest.mark.asyncio
async def test_runner_requires_xai_and_apify_keys(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("APIFY_TOKEN", "apify-key")
    runner = XaiMultiAgentV1Runner(client_factory=lambda **_: _Client(_response()))
    state = ScanState(scan_id="sid", subject="Jane", config=ScanConfig())

    with pytest.raises(Exception, match="XAI_API_KEY"):
        await runner.run(
            subject="Jane",
            state=state,
            llm=MagicMock(),
            tools=[],
            cost_cb=MagicMock(),
        )

    monkeypatch.setenv("XAI_API_KEY", "xai-key")
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    with pytest.raises(Exception, match="APIFY_TOKEN"):
        await runner.run(
            subject="Jane",
            state=state,
            llm=MagicMock(),
            tools=[],
            cost_cb=MagicMock(),
        )
