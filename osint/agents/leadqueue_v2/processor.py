"""Processor: runs one Lead through a small ReAct mini-loop and parses
the LLM's structured output into (findings, new_leads).

The mini-loop is bounded by `max_processor_tool_calls` (default 5) so
a single lead can't burn the whole scan budget.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from osint.agents.leadqueue_v2.prompts import (
    PROCESSOR_SYSTEM,
    format_findings_compact,
)
from osint.agents.leadqueue_v2.queue import Finding, Lead, Source

_FENCED_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_processor_output(text: str, lead: Lead) -> tuple[list[Finding], list[Lead]]:
    """Parse the LLM's terminal message into (findings, new_leads).

    Raises ValueError if the JSON envelope is missing or malformed —
    caller decides whether to retry."""
    m = _FENCED_JSON.search(text)
    if not m:
        # Fall back: try a bare JSON object at the end of the message.
        stripped = text.strip()
        if not (stripped.startswith("{") and stripped.endswith("}")):
            raise ValueError("processor output missing fenced JSON envelope")
        body = stripped
    else:
        body = m.group(1)
    data = json.loads(body)

    findings: list[Finding] = []
    for f in data.get("findings", []):
        findings.append(Finding(
            id=f.get("id") or f"f-{uuid.uuid4().hex[:8]}",
            claim=f["claim"],
            evidence=[Source(**e) for e in f["evidence"]],
            confidence=f["confidence"],
            lead_id=lead.id,
            tags=f.get("tags", []),
        ))

    new_leads: list[Lead] = []
    now = datetime.now(timezone.utc)
    for nl in data.get("new_leads", []):
        new_leads.append(Lead(
            id=f"l-{uuid.uuid4().hex[:8]}",
            kind=nl["kind"],
            description=nl["description"],
            priority=int(nl["priority"]),
            depth=lead.depth + 1,
            parent_lead_id=lead.id,
            created_at=now,
        ))
    return findings, new_leads


async def process_one_lead(
    *,
    subject: str,
    lead: Lead,
    all_findings: list[Finding],
    llm: BaseChatModel,
    tools: list[Any],
    cost_cb: Any,
    max_processor_tool_calls: int = 5,
) -> tuple[list[Finding], list[Lead]]:
    """Process one Lead. Returns (findings, new_leads)."""
    findings_summary = format_findings_compact(all_findings)
    user_msg = (
        f"SUBJECT:\n{subject}\n\n"
        f"LEAD ({lead.kind}, priority={lead.priority}, depth={lead.depth}):\n"
        f"{lead.description}\n\n"
        f"FINDINGS SO FAR:\n{findings_summary or '(none)'}\n\n"
        f"Investigate this lead. Use AT MOST {max_processor_tool_calls} tool calls. "
        f"Return findings + new_leads as a single JSON envelope per the system prompt."
    )

    messages = [
        SystemMessage(content=PROCESSOR_SYSTEM),
        HumanMessage(content=user_msg),
    ]

    # Only forward cost_cb to LangGraph if it's an actual callback handler.
    # Tests use MagicMock() as a stand-in, and MagicMock falls into
    # LangGraph's "tap_output" path and silently eats the agent's output —
    # so we gate on isinstance to keep production wiring (LLMCostCallback)
    # while letting tests pass MagicMock without breaking the agent loop.
    invoke_callbacks: list[Any] = (
        [cost_cb] if isinstance(cost_cb, BaseCallbackHandler) else []
    )

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            agent = create_react_agent(model=llm, tools=tools, prompt=None)
            result = await agent.ainvoke(
                {"messages": messages},
                config={
                    "callbacks": invoke_callbacks,
                    "recursion_limit": max_processor_tool_calls * 2 + 5,
                },
            )
            # Last AI message holds the structured output
            last_ai = next(
                (m for m in reversed(result.get("messages", []))
                 if m.__class__.__name__ == "AIMessage"),
                None,
            )
            text = (getattr(last_ai, "content", "") or "") if last_ai else ""
            return _parse_processor_output(text, lead)
        except Exception as e:  # parsing failed OR LangGraph errored
            last_error = e
            continue

    # Both attempts failed — consume the lead silently.
    return [], []
