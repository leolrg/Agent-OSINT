"""End-to-end tests for CriticReactV3Runner using BindableFake.

The runner alternates create_react_agent.ainvoke() (which consumes one
LLM response per call when the model emits no tool_calls) with critic()
(also one LLM response per call). Tests pin the engagement→critic cycle.
"""
from unittest.mock import MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from osint.agents.critic_react_v3.runner import CriticReactV3Runner
from osint.state import ScanState, StopReason
from osint.types import ScanConfig


class BindableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self


HAPPY_DRAFT = (
    '```json\n{"open": [], "answered": ["Q1"], "dropped": []}\n```\n\n'
    '**Executive Summary**\n\nJane works at Acme.\n\n'
    '```json\n{"extracted_identifiers": {"employers": ["Acme"]}}\n```'
)


async def test_runner_happy_path_first_engagement_accepted():
    """Engagement 1 emits empty-`open` ledger + final report -> critic ACCEPT -> done."""
    fake = BindableFake(responses=[
        AIMessage(content=HAPPY_DRAFT, tool_calls=[]),  # engagement 1 final
        AIMessage(content="VERDICT: ACCEPT\n"),         # critic
    ])
    state = ScanState(
        scan_id="x",
        subject="Jane",
        config=ScanConfig(agent_version="critic_react_v3", preset="coffee_career"),
    )
    runner = CriticReactV3Runner()
    parsed, stop_reason = await runner.run(
        subject="Jane",
        state=state,
        llm=fake,
        tools=[],
        cost_cb=MagicMock(),
    )
    assert stop_reason is None
    assert parsed["extracted_identifiers"] == {"employers": ["Acme"]}
    assert "Jane works at Acme" in parsed["report"]["text"]


# ---------- T10 ----------

LEDGER_NONEMPTY_DRAFT = (
    '```json\n{"open": ["What is current employer?"], "answered": [], "dropped": []}\n```\n\n'
    'Partial findings...'
)


async def test_runner_ledger_non_empty_retries_then_accepts():
    """Engagement 1 emits non-empty `open` -> orchestrator appends synthetic
    user msg -> Engagement 2 emits empty `open` + final report -> critic ACCEPT."""
    fake = BindableFake(responses=[
        AIMessage(content=LEDGER_NONEMPTY_DRAFT, tool_calls=[]),  # engagement 1 (still open)
        AIMessage(content=HAPPY_DRAFT, tool_calls=[]),            # engagement 2 (empty open)
        AIMessage(content="VERDICT: ACCEPT\n"),                   # critic
    ])
    state = ScanState(
        scan_id="x", subject="Jane",
        config=ScanConfig(agent_version="critic_react_v3"),
    )
    runner = CriticReactV3Runner()
    parsed, stop_reason = await runner.run(
        subject="Jane", state=state, llm=fake, tools=[], cost_cb=MagicMock(),
    )
    assert stop_reason is None
    assert parsed["extracted_identifiers"] == {"employers": ["Acme"]}


# ---------- T11 ----------

HAPPY_DRAFT_2 = (
    '```json\n{"open": [], "answered": ["Q1","Q2"], "dropped": []}\n```\n\n'
    '**Executive Summary**\n\nJane works at Acme; current title VP Eng.\n\n'
    '```json\n{"extracted_identifiers": {"employers": ["Acme"], "name_variations": ["Jane Doe"]}}\n```'
)


async def test_runner_critic_reject_then_accept_after_one_revision():
    """Engagement 1 empty-open -> critic REJECT -> engagement 2 empty-open -> critic ACCEPT."""
    fake = BindableFake(responses=[
        AIMessage(content=HAPPY_DRAFT, tool_calls=[]),                           # engagement 1
        AIMessage(content="VERDICT: REJECT\nGAPS:\n- No title for current role\n"),  # critic 1
        AIMessage(content=HAPPY_DRAFT_2, tool_calls=[]),                         # engagement 2
        AIMessage(content="VERDICT: ACCEPT\n"),                                  # critic 2
    ])
    state = ScanState(
        scan_id="x", subject="Jane",
        config=ScanConfig(agent_version="critic_react_v3", max_critic_rejections=3),
    )
    parsed, stop_reason = await CriticReactV3Runner().run(
        subject="Jane", state=state, llm=fake, tools=[], cost_cb=MagicMock(),
    )
    assert stop_reason is None
    assert "VP Eng" in parsed["report"]["text"]


# ---------- T12 ----------

async def test_runner_critic_exhaustion_returns_last_draft_with_critic_exhausted():
    """max_critic_rejections=1: 1 engagement, REJECT, 1 more engagement, REJECT
    -> rejections=2 > 1 -> return last draft with CRITIC_EXHAUSTED."""
    fake = BindableFake(responses=[
        AIMessage(content=HAPPY_DRAFT, tool_calls=[]),                # engagement 1
        AIMessage(content="VERDICT: REJECT\nGAPS:\n- gap a\n"),       # critic 1 (rejection 1)
        AIMessage(content=HAPPY_DRAFT_2, tool_calls=[]),              # engagement 2
        AIMessage(content="VERDICT: REJECT\nGAPS:\n- gap b\n"),       # critic 2 (rejection 2 > cap)
    ])
    state = ScanState(
        scan_id="x", subject="Jane",
        config=ScanConfig(agent_version="critic_react_v3", max_critic_rejections=1),
    )
    parsed, stop_reason = await CriticReactV3Runner().run(
        subject="Jane", state=state, llm=fake, tools=[], cost_cb=MagicMock(),
    )
    assert stop_reason == StopReason.CRITIC_EXHAUSTED
    # Returned draft is the LAST one the agent produced.
    assert "VP Eng" in parsed["report"]["text"]
    assert parsed["extracted_identifiers"]["employers"] == ["Acme"]


# ---------- T13 ----------

async def test_runner_empty_final_falls_through_to_cap_cut_synthesis():
    """If the agent emits an empty AIMessage as terminal, runner falls
    through to v1's _synthesize. The synthesizer's response becomes the
    final report."""
    SYNTH_FALLBACK = (
        '**Executive Summary**\n\nJane (cap-cut).\n\n'
        '```json\n{"extracted_identifiers": {"employers": ["Acme"]}}\n```'
    )
    fake = BindableFake(responses=[
        AIMessage(content="", tool_calls=[]),               # engagement 1 EMPTY -> EMPTY_FINAL
        AIMessage(content=SYNTH_FALLBACK, tool_calls=[]),   # _synthesize
    ])
    state = ScanState(
        scan_id="x", subject="Jane",
        config=ScanConfig(agent_version="critic_react_v3"),
    )
    parsed, stop_reason = await CriticReactV3Runner().run(
        subject="Jane", state=state, llm=fake, tools=[], cost_cb=MagicMock(),
    )
    assert stop_reason == StopReason.EMPTY_FINAL
    assert "cap-cut" in parsed["report"]["text"]
    assert parsed["extracted_identifiers"]["employers"] == ["Acme"]
