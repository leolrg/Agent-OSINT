"""Processor: takes one Lead + the running findings record, runs a
small ReAct mini-loop, returns structured (findings, new_leads)."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from osint.agents.leadqueue_v2.processor import process_one_lead
from osint.agents.leadqueue_v2.queue import Finding, Lead


class BindableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self


def _lead(description: str = "investigate X", priority: int = 50) -> Lead:
    return Lead(
        id="l-test",
        kind="test_kind",
        description=description,
        priority=priority,
        depth=0,
        parent_lead_id=None,
        created_at=datetime.now(timezone.utc),
    )


def _ai_with_json(payload: dict) -> AIMessage:
    """Wrap a dict in the prose-then-fenced-JSON form processor expects."""
    import json
    return AIMessage(
        content=f"Processed.\n\n```json\n{json.dumps(payload)}\n```\n",
        tool_calls=[],
    )


async def test_process_one_lead_parses_findings_and_new_leads():
    """Happy path: LLM emits structured JSON; processor returns
    typed (findings, new_leads)."""
    payload = {
        "findings": [
            {
                "claim": "subject's IG handle is simonwen.eth",
                "evidence": [
                    {"tool_call_id": "tc-1", "snippet_quote": "instagram.com/simonwen.eth"}
                ],
                "confidence": "high",
                "tags": ["handle", "instagram"],
            }
        ],
        "new_leads": [
            {
                "kind": "investigate_handle",
                "description": "fetch simonwen.eth IG profile",
                "priority": 70,
            }
        ],
    }
    fake = BindableFake(responses=[_ai_with_json(payload)])
    findings, new_leads = await process_one_lead(
        subject="Jane",
        lead=_lead(),
        all_findings=[],
        llm=fake,
        tools=[],
        cost_cb=MagicMock(),
    )
    assert len(findings) == 1
    assert findings[0].claim == "subject's IG handle is simonwen.eth"
    assert findings[0].lead_id == "l-test"
    assert findings[0].evidence[0].tool_call_id == "tc-1"
    assert len(new_leads) == 1
    assert new_leads[0].description == "fetch simonwen.eth IG profile"
    assert new_leads[0].priority == 70
    assert new_leads[0].depth == 1, "lead depth must increment from parent's depth"
    assert new_leads[0].parent_lead_id == "l-test"


async def test_process_one_lead_handles_malformed_json_with_retry():
    """If the LLM returns malformed JSON, processor retries once.
    On second failure, it returns empty findings + empty new_leads
    (lead is consumed, not requeued — sticky-error guard)."""
    fake = BindableFake(responses=[
        AIMessage(content="not json at all", tool_calls=[]),
        AIMessage(content="still not json", tool_calls=[]),
    ])
    findings, new_leads = await process_one_lead(
        subject="Jane",
        lead=_lead(),
        all_findings=[],
        llm=fake,
        tools=[],
        cost_cb=MagicMock(),
    )
    assert findings == []
    assert new_leads == []


async def test_process_one_lead_recovers_on_retry():
    """LLM returns malformed JSON first, valid JSON on retry → success."""
    payload = {
        "findings": [
            {
                "claim": "subject went to NYU",
                "evidence": [{"tool_call_id": "tc-1", "snippet_quote": "..."}],
                "confidence": "medium",
                "tags": ["education"],
            }
        ],
        "new_leads": [],
    }
    fake = BindableFake(responses=[
        AIMessage(content="garbage", tool_calls=[]),
        _ai_with_json(payload),
    ])
    findings, new_leads = await process_one_lead(
        subject="Jane",
        lead=_lead(),
        all_findings=[],
        llm=fake,
        tools=[],
        cost_cb=MagicMock(),
    )
    assert len(findings) == 1
    assert findings[0].confidence == "medium"
