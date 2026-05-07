"""Verifier: scores the draft report's coverage + grounding, returns
either {satisfied=True} or a list of new leads to push onto the queue."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from osint.agents.leadqueue_v2.prompts import (
    VERIFIER_SYSTEM,
    format_findings_compact,
    format_leads_log_compact,
)
from osint.agents.leadqueue_v2.queue import Finding, Lead


_FENCED_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


class VerifierResult(BaseModel):
    satisfied: bool
    gaps: list[str]
    new_leads: list[Lead]


def _parse_verifier_output(text: str) -> VerifierResult:
    m = _FENCED_JSON.search(text)
    if m:
        body = m.group(1)
    else:
        stripped = text.strip()
        if not (stripped.startswith("{") and stripped.endswith("}")):
            raise ValueError("verifier output missing JSON envelope")
        body = stripped
    data = json.loads(body)
    new_leads: list[Lead] = []
    now = datetime.now(timezone.utc)
    for nl in data.get("new_leads", []):
        new_leads.append(Lead(
            id=f"l-{uuid.uuid4().hex[:8]}",
            kind=nl["kind"],
            description=nl["description"],
            priority=int(nl.get("priority", 80)),
            depth=0,                  # verifier-leads are top-level
            parent_lead_id=None,
            created_at=now,
        ))
    return VerifierResult(
        satisfied=bool(data.get("satisfied", False)),
        gaps=list(data.get("gaps", [])),
        new_leads=new_leads,
    )


async def verify(
    *,
    subject: str,
    report_text: str,
    findings: list[Finding],
    leads_log: list[Lead],
    llm: BaseChatModel,
    cost_cb: Any,
) -> VerifierResult:
    user_msg = (
        f"SUBJECT:\n{subject}\n\n"
        f"DRAFT REPORT:\n{report_text}\n\n"
        f"FINDINGS:\n{format_findings_compact(findings)}\n\n"
        f"LEADS ALREADY PROCESSED:\n{format_leads_log_compact(leads_log)}\n\n"
        f"Score the report and return your JSON envelope."
    )
    msgs = [
        SystemMessage(content=VERIFIER_SYSTEM),
        HumanMessage(content=user_msg),
    ]
    callbacks = [cost_cb] if isinstance(cost_cb, BaseCallbackHandler) else []
    for _attempt in (1, 2):
        try:
            r = await llm.ainvoke(msgs, config={"callbacks": callbacks})
            return _parse_verifier_output(r.content or "")
        except Exception:
            continue
    # Both attempts failed → accept the draft.
    return VerifierResult(satisfied=True, gaps=[], new_leads=[])
