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
        tool_calls=[],
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
    parsed = await synthesize(
        subject="Jane", findings=[], tool_calls=[], llm=fake, cost_cb=MagicMock(),
    )
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
    parsed = await synthesize(
        subject="Jane", findings=[_f("X")], tool_calls=[], llm=fake, cost_cb=MagicMock(),
    )
    assert "Executive Summary" in parsed["report"]["text"]


async def test_synthesize_promotes_raw_snippet_handle_when_findings_miss_it():
    """Regression for the Zhihu inline-handle leak: the processor judged
    a snippet as identity-mismatch and recorded no finding for it, but
    the snippet contained `xhs/twitter:semona0x`. The synthesizer reads
    the raw tool_calls block and surfaces the handle in the final report.

    We don't run a real LLM — we hand-craft an AIMessage that quotes the
    snippet, then verify it lands in the parsed report. This pins the
    plumbing (tool_calls reaches the prompt + the parser surfaces it),
    not real LLM behavior. Beyond the round-trip, we also spy on the
    LLM input itself to ensure format_tool_calls_compact actually wrote
    `Semona0x` and `tc-zhihu` into the user message — without that
    assertion, this test would still pass even if the tool_calls block
    were silently dropped from the prompt."""
    # Canned tool_call: a tavily_search result with the inline reveal.
    canned_tool_call = {
        "tool": "tavily_search",
        "tool_call_id": "tc-zhihu",
        "input": {"query": "Simon Wen NYU"},
        "output": {
            "results": [
                {
                    "url": "https://www.zhihu.com/people/simonwen",
                    "title": "Simon Wen — Zhihu",
                    "content": "Simon Wen. 纽约｜05｜大一xhs/twitter:Semona0x",
                },
            ],
        },
    }
    promoted_report = (
        "**Executive Summary**\n\nSubject identified.\n\n"
        "**Digital & Social Media Footprint**\n"
        "- Twitter / xhs handle: `Semona0x` (medium confidence) — inline "
        "reveal in Zhihu snippet, source [tc-zhihu]: "
        "\"Simon Wen. 纽约｜05｜大一xhs/twitter:Semona0x\".\n\n"
        "```json\n{\"extracted_identifiers\": {\"usernames\": [\"Semona0x\"]}}\n```"
    )

    # Spy on what the synthesizer actually feeds into the LLM. We don't
    # subclass FakeMessagesListChatModel here because pydantic-validated
    # field discipline makes ad-hoc capture attributes awkward; a plain
    # AsyncMock with bind_tools+ainvoke is cleaner.
    captured: list[list] = []

    async def fake_ainvoke(msgs, config=None, **kwargs):
        captured.append(list(msgs))
        return AIMessage(content=promoted_report, tool_calls=[])

    fake = MagicMock()
    fake.bind_tools = MagicMock(return_value=fake)
    fake.ainvoke = AsyncMock(side_effect=fake_ainvoke)

    parsed = await synthesize(
        subject="Simon Wen",
        findings=[],          # processor missed it: no finding recorded
        tool_calls=[canned_tool_call],
        llm=fake,
        cost_cb=MagicMock(),
    )

    # 1. Plumbing assertion: format_tool_calls_compact must have rendered
    #    the handle and tool_call_id into the user-message content.
    assert captured, "synthesize() should have called llm.ainvoke at least once"
    sent_msgs = captured[0]
    user_msg_content = next(
        m.content for m in sent_msgs if m.__class__.__name__ == "HumanMessage"
    )
    assert "Semona0x" in user_msg_content, (
        "tool_calls block must reach the synthesizer's user message"
    )
    assert "tc-zhihu" in user_msg_content, "tool_call_id must reach the prompt"

    # 2. Round-trip assertion: the handle must end up in the parsed
    #    report text and the extracted-identifiers tail JSON.
    assert "Semona0x" in parsed["report"]["text"]
    assert parsed["extracted_identifiers"] == {"usernames": ["Semona0x"]}
