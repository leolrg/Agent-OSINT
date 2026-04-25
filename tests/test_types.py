from osint.types import LLMConfig, LLMPricing, ScanConfig


def test_scanconfig_defaults():
    c = ScanConfig()
    assert c.enabled_tools == {"tavily_search", "tavily_extract", "maigret"}
    assert c.budget_usd == 5.0
    assert c.max_tool_calls == 30
    assert c.max_wall_clock_sec == 600
    assert c.tool_concurrency == {"maigret": 2}
    assert c.tool_options == {}
    # grok-4.20 default pointing at xAI's OpenAI-compatible endpoint.
    assert c.llm.model == "grok-4.20"
    assert c.llm.base_url == "https://api.x.ai/v1"
    assert c.llm.api_key_env_var == "XAI_API_KEY"
    assert c.llm.pricing.input_per_mtok_usd == 2.0
    assert c.llm.pricing.output_per_mtok_usd == 6.0


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
        enabled_tools={"tavily_search"},
        budget_usd=1.0,
        tool_options={"maigret": {"proxy_url": "http://p:8080"}},
    )
    assert c.enabled_tools == {"tavily_search"}
    assert c.tool_options["maigret"]["proxy_url"] == "http://p:8080"
