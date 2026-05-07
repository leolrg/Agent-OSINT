"""Agent-runner protocol shared by v1 (ReAct) and v2 (lead-queue).

Each agent runner takes a fully-prepared scan context (subject, config,
LLM, ScanState, CappedTools, cost callback) and produces a parsed
report + an optional StopReason. Persistence (writing scan JSON/MD) is
handled by the dispatcher in osint/run.py — runners do NOT write files.
"""
from __future__ import annotations

from typing import Any, Literal, Optional, Protocol

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from osint.state import ScanState, StopReason


class AgentRunner(Protocol):
    """Stable surface every agent version must implement."""

    async def run(
        self,
        *,
        subject: str,
        state: ScanState,
        llm: BaseChatModel,
        tools: list[Any],
        cost_cb: Any,
    ) -> tuple[dict, StopReason | None]:
        """Run the agent.

        Returns a 2-tuple:
          - parsed_report: {"extracted_identifiers": {...}, "report": {...}}
          - stop_reason:   StopReason | None  (None on a normal finish)

        Side effects: mutates `state` (records tool calls, messages, etc.).
        Persistence: caller's responsibility.
        """
        ...


# ============================================================================
# Agent parameter manifests.
# ============================================================================
# Each agent ships a `manifest.py` declaring its user-facing parameter schema.
# The catalog is exposed via FastAPI `GET /api/agents` and consumed by the
# Next.js new-scan form to render fields dynamically. The same manifest is
# the source of truth for the worker's _build_config translation so frontend
# and backend can never drift on param names/types.

ParamType = Literal["select", "text", "int", "float", "bool"]


class ParamField(BaseModel):
    """One user-facing parameter field. Renders as a form input in the UI."""
    name: str = Field(description="Internal field name passed in SQS params.")
    label: str = Field(description="User-facing label shown in the form.")
    type: ParamType
    default: Any = None
    options: Optional[list[str]] = Field(
        default=None,
        description="For type='select': allowed values.",
    )
    help: Optional[str] = Field(default=None, description="Inline help text.")
    advanced: bool = Field(
        default=False,
        description="Hidden behind an 'advanced' collapsible by default.",
    )
    min: Optional[float] = Field(default=None, description="Numeric min.")
    max: Optional[float] = Field(default=None, description="Numeric max.")


class AgentManifest(BaseModel):
    """An agent's user-facing description + parameter schema."""
    name: str = Field(
        description="Internal agent name matching osint.agents.AGENTS key."
    )
    display_name: str = Field(description="UI label, e.g. 'ReAct'.")
    description: str = Field(description="One-line UI description.")
    estimated_duration: str = Field(
        description="Human-readable estimate, e.g. '~3-10 min'."
    )
    params: list[ParamField] = Field(
        default_factory=list,
        description="Agent-specific parameters (excluding common base params).",
    )


COMMON_PARAMS: list[ParamField] = [
    ParamField(
        name="budget_usd", label="Budget (USD)", type="float",
        default=0.50, min=0.10, max=20.0, advanced=True,
        help="Hard cost ceiling. Scan stops if exceeded.",
    ),
    ParamField(
        name="max_tool_calls", label="Max tool calls", type="int",
        default=100, min=1, max=500, advanced=True,
    ),
    ParamField(
        name="max_wall_clock_sec", label="Max wall-clock (seconds)", type="int",
        default=600, min=30, max=7200, advanced=True,
    ),
]
