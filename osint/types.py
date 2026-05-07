from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, NonNegativeFloat, NonNegativeInt, PositiveFloat, PositiveInt, model_validator


def default_enabled_tools() -> set[str]:
    return {"web_search", "web_extract", "maigret"}


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
    # Which agent implementation to dispatch to. `react_v1` is the original
    # single-agent ReAct loop; `leadqueue_v2` is the lead-queue-driven
    # planner/verifier pipeline introduced for v2. Validated at dispatch
    # time against the AGENTS registry (see osint/run.py); kept as a plain
    # `str` so the dispatcher raises a ScanConfigError with a useful
    # message (listing known versions) rather than a pydantic
    # ValidationError on construction.
    agent_version: str = "react_v1"
    # Cap on verifier passes per lead in `leadqueue_v2`. Ignored by
    # `react_v1`. Prevents runaway loops when a lead never converges.
    max_verifier_iterations: PositiveInt = 3
    # Per-lead tool-call ceiling for the leadqueue_v2 processor's mini-ReAct
    # loop. Higher → each lead can pivot deeper before the synthesizer runs;
    # lower → tighter cost per lead but shallower investigation. Ignored by
    # `react_v1` and `xai_multiagent_v1`. The whole-scan `max_tool_calls`
    # still applies — this is a per-lead inner cap, not a global one.
    max_processor_tool_calls: PositiveInt = 5
    # Free-form goal text and named preset for critic_react_v3. Both are
    # honored; the preset preamble and the goal are concatenated into the
    # system prompt in that order. Ignored by react_v1 / leadqueue_v2 /
    # xai_multiagent_v1.
    goal: str = ""
    preset: Literal[
        "coffee_career",
        "coffee_personal",
        "reconnect",
        "sales_outreach",
        "dossier",
        "general",
    ] = "general"
    # critic_react_v3 only: cap on critic rejection rounds and per-engagement
    # LangGraph recursion limit.
    max_critic_rejections: PositiveInt = 3
    max_recursion_per_engagement: PositiveInt = 50
    # critic_react_v3 only: hard floor on the total tool-call count below
    # which the critic's ACCEPT verdict is overridden to REJECT (regardless
    # of draft quality). Default 0 — no floor. Set higher (e.g. 25–40)
    # when you want guaranteed depth on a dossier.
    min_tool_calls: NonNegativeInt = 0
    # critic_react_v3 only: minimum number of critic rejection rounds
    # before any ACCEPT verdict can terminate the loop. Default 0 — no
    # floor. Set 1+ to force at least N+1 engagements regardless of how
    # quickly the critic is satisfied. Must be <= max_critic_rejections.
    min_critic_rejections: NonNegativeInt = 0

    @model_validator(mode="after")
    def _check_critic_rejection_floor_below_cap(self) -> "ScanConfig":
        if self.min_critic_rejections > self.max_critic_rejections:
            raise ValueError(
                f"min_critic_rejections ({self.min_critic_rejections}) must be "
                f"<= max_critic_rejections ({self.max_critic_rejections}); "
                f"otherwise the loop deadlocks."
            )
        return self


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
    # v2 lead-queue artifacts. Empty list for v1 (back-compat). Stored as
    # plain dicts (already serialized via Pydantic's `model_dump(mode="json")`)
    # so this module doesn't need to import the v2 Lead/Finding models.
    findings: list[dict] = Field(default_factory=list)   # serialized list[Finding]
    leads_log: list[dict] = Field(default_factory=list)  # serialized list[Lead]
