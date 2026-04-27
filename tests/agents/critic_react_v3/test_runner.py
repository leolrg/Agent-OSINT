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
