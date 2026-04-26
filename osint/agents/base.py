"""Agent-runner protocol shared by v1 (ReAct) and v2 (lead-queue).

Each agent runner takes a fully-prepared scan context (subject, config,
LLM, ScanState, CappedTools, cost callback) and produces a parsed
report + an optional StopReason. Persistence (writing scan JSON/MD) is
handled by the dispatcher in osint/run.py — runners do NOT write files.
"""
from __future__ import annotations

from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel

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
