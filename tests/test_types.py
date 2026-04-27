from osint.types import LLMConfig, LLMPricing, ScanConfig


def test_scanconfig_defaults():
    c = ScanConfig()
    assert c.enabled_tools == {"web_search", "web_extract", "maigret"}
    assert c.budget_usd == 5.0
    assert c.max_tool_calls == 30
    assert c.max_wall_clock_sec == 600
    assert c.tool_options == {}
    # Single-pass by default — back-compat with all pre-deepen scans.
    assert c.passes == 1
    # grok-4.20 default pointing at xAI's OpenAI-compatible endpoint.
    assert c.llm.model == "grok-4.20"
    assert c.llm.base_url == "https://api.x.ai/v1"
    assert c.llm.api_key_env_var == "XAI_API_KEY"
    assert c.llm.pricing.input_per_mtok_usd == 2.0
    assert c.llm.pricing.output_per_mtok_usd == 6.0
    assert c.agent_version == "react_v1"
    assert c.max_verifier_iterations == 3


def test_scanconfig_rejects_nonpositive_caps():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ScanConfig(budget_usd=0)
    with pytest.raises(ValidationError):
        ScanConfig(max_tool_calls=0)
    with pytest.raises(ValidationError):
        ScanConfig(max_wall_clock_sec=0)


def test_scanconfig_swap_llm_to_openai_gpt():
    """ScanConfig accepts any OpenAI-compatible chat completions endpoint."""
    c = ScanConfig(
        llm=LLMConfig(
            model="gpt-5",
            base_url="https://api.openai.com/v1",
            api_key_env_var="OPENAI_API_KEY",
            pricing=LLMPricing(input_per_mtok_usd=2.50, output_per_mtok_usd=10.0),
        ),
    )
    assert c.llm.model == "gpt-5"
    assert c.llm.api_key_env_var == "OPENAI_API_KEY"
    assert c.llm.pricing.input_per_mtok_usd == 2.50


def test_scanconfig_overrides_other_fields():
    c = ScanConfig(
        enabled_tools={"web_search"},
        budget_usd=1.0,
        tool_options={"maigret": {"proxy_url": "http://p:8080"}},
    )
    assert c.enabled_tools == {"web_search"}
    assert c.tool_options["maigret"]["proxy_url"] == "http://p:8080"


from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from osint.types import ToolCallRecord, ScanResult


def test_toolcallrecord_defaults():
    now = datetime(2026, 4, 24)
    tc = ToolCallRecord(
        turn=1, tool="web_search", tool_call_id="call_a",
        input={"query": "x"}, output={"results": []}, raw={"results": []},
        started_at=now, completed_at=now, cost_usd=0.004,
    )
    assert tc.error is None


def test_scanresult_fields():
    s = ScanResult(
        scan_id="s1", subject="Jane Doe",
        extracted_identifiers={"emails": ["j@e"]},
        report={"summary": "..."},
        tool_calls=[], total_cost_usd=0.0, duration_sec=1.0,
        path=Path("/tmp/s1.json"),
    )
    assert s.subject == "Jane Doe"
    assert s.path.name == "s1.json"


def test_scan_config_defaults_for_critic_react_v3_fields():
    c = ScanConfig()
    assert c.goal == ""
    assert c.preset == "general"
    assert c.max_critic_rejections == 3
    assert c.max_recursion_per_engagement == 50


def test_scan_config_preset_must_be_known():
    with pytest.raises(ValidationError):
        ScanConfig(preset="not_a_real_preset")  # type: ignore[arg-type]


def test_scan_config_goal_accepts_free_form_string():
    c = ScanConfig(goal="coffee chat about ML infra")
    assert c.goal == "coffee chat about ML infra"
