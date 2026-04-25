from pydantic import BaseModel, Field, NonNegativeFloat, PositiveFloat, PositiveInt


def default_enabled_tools() -> set[str]:
    return {"tavily_search", "tavily_extract", "maigret"}


def default_tool_concurrency() -> dict[str, int]:
    return {"maigret": 2}


class LLMPricing(BaseModel):
    """Per-million-token pricing used to convert usage_metadata into USD."""
    input_per_mtok_usd: NonNegativeFloat = 2.0   # grok-4.20 default, 2026-04 per xAI docs
    output_per_mtok_usd: NonNegativeFloat = 6.0


class LLMConfig(BaseModel):
    """Configuration for the main agent LLM.

    Any provider exposing an OpenAI-compatible /v1/chat/completions endpoint
    works (xAI Grok, OpenAI GPT, DeepSeek, Together, Groq, Ollama, vLLM,
    llama.cpp's server, ...). Defaults target xAI's Grok 4.20.
    """
    model: str = "grok-4.20"
    base_url: str = "https://api.x.ai/v1"
    api_key_env_var: str = "XAI_API_KEY"
    pricing: LLMPricing = Field(default_factory=LLMPricing)


class ScanConfig(BaseModel):
    enabled_tools: set[str] = Field(default_factory=default_enabled_tools)
    budget_usd: PositiveFloat = 5.0
    max_tool_calls: PositiveInt = 30
    max_wall_clock_sec: PositiveInt = 600
    tool_concurrency: dict[str, int] = Field(default_factory=default_tool_concurrency)
    tool_options: dict[str, dict] = Field(default_factory=dict)
    llm: LLMConfig = Field(default_factory=LLMConfig)
