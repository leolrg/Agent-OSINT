from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, NonNegativeFloat, PositiveFloat, PositiveInt


def default_enabled_tools() -> set[str]:
    return {"tavily_search", "tavily_extract", "maigret"}


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
    tool_options: dict[str, dict] = Field(default_factory=dict)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    # Number of agent passes per scan. Pass 1 is the initial investigation;
    # passes 2..N are "deepen" passes that receive the previous pass's draft
    # report and explicitly look for gaps / shallow sections / unfollowed
    # leads to extend. Budget, max_tool_calls, and max_wall_clock_sec apply
    # to the WHOLE scan (not per-pass) so the cap is honoured no matter
    # how many passes are configured.
    passes: PositiveInt = 1


class ToolCallRecord(BaseModel):
    turn: int
    tool: str
    tool_call_id: str | None = None    # matches LangGraph's tool_calls[].id
    input: dict[str, Any]
    output: dict[str, Any] | None
    raw: Any
    started_at: datetime
    completed_at: datetime
    cost_usd: float
    error: str | None = None


class ScanResult(BaseModel):
    scan_id: str
    subject: str
    extracted_identifiers: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    duration_sec: float = 0.0
    path: Path                              # the .json artifact (source of truth)
    markdown_path: Path | None = None       # the .md sibling (human-readable render)
