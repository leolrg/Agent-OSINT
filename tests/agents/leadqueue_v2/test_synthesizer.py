from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from osint.agents.leadqueue_v2.queue import Finding, Source
from osint.agents.leadqueue_v2.synthesizer import synthesize


class BindableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self


def _f(claim: str, conf: str = "high", tags: list[str] | None = None) -> Finding:
    return Finding(
        id="f-1",
        claim=claim,
        evidence=[Source(tool_call_id="tc-1", snippet_quote="evidence text")],
        confidence=conf,
        lead_id="l-1",
        tags=tags or [],
    )


REPORT_TEXT = """**Executive Summary**

Jane is a SWE in NYC.

**Sources**
- tc-1: ...

```json
{"extracted_identifiers": {"emails": ["jane@example.com"]}}
```
"""


async def test_synthesize_passes_findings_to_llm_and_returns_parsed_report():
    fake = BindableFake(responses=[AIMessage(content=REPORT_TEXT, tool_calls=[])])
    parsed = await synthesize(
        subject="Jane",
        findings=[_f("Jane is a SWE", tags=["career"])],
        llm=fake,
        cost_cb=MagicMock(),
    )
    assert "Executive Summary" in parsed["report"]["text"]
    assert parsed["extracted_identifiers"] == {"emails": ["jane@example.com"]}


async def test_synthesize_handles_empty_findings():
    """If there are no findings, synthesizer still returns a parseable
    report — don't crash on a sparse subject."""
    fake = BindableFake(responses=[
        AIMessage(content="Nothing found.\n\n```json\n{\"extracted_identifiers\": {}}\n```",
                  tool_calls=[])
    ])
    parsed = await synthesize(subject="Jane", findings=[], llm=fake, cost_cb=MagicMock())
    assert parsed["extracted_identifiers"] == {}
    assert "Nothing found" in parsed["report"]["text"]


async def test_synthesize_falls_back_when_llm_returns_empty_content():
    """Grok-4.20's reasoning-mode 0-token bug — second call must still
    produce SOMETHING. Synthesizer retries once, then returns whatever
    text it has (even empty) wrapped in the standard parsed shape."""
    fake = BindableFake(responses=[
        AIMessage(content="", tool_calls=[]),     # first attempt: empty
        AIMessage(content=REPORT_TEXT, tool_calls=[]),  # retry: real
    ])
    parsed = await synthesize(subject="Jane", findings=[_f("X")], llm=fake, cost_cb=MagicMock())
    assert "Executive Summary" in parsed["report"]["text"]
