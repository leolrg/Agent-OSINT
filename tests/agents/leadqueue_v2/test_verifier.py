from datetime import datetime, timezone
from unittest.mock import MagicMock

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from osint.agents.leadqueue_v2.queue import Finding, Lead, Source
from osint.agents.leadqueue_v2.verifier import VerifierResult, verify


class BindableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self


def _f(claim: str) -> Finding:
    return Finding(
        id="f-1",
        claim=claim,
        evidence=[Source(tool_call_id="tc-1", snippet_quote="...")],
        confidence="high",
        lead_id="l-1",
        tags=[],
    )


def _l(description: str) -> Lead:
    return Lead(
        id="l-1",
        kind="test",
        description=description,
        priority=50,
        depth=0,
        parent_lead_id=None,
        created_at=datetime.now(timezone.utc),
    )


def _ai(json_body: str) -> AIMessage:
    return AIMessage(content=f"```json\n{json_body}\n```", tool_calls=[])


async def test_verifier_satisfied_returns_no_new_leads():
    fake = BindableFake(responses=[
        _ai('{"satisfied": true, "gaps": [], "new_leads": []}')
    ])
    result = await verify(
        subject="Jane",
        report_text="ok",
        findings=[_f("X")],
        leads_log=[_l("a")],
        llm=fake,
        cost_cb=MagicMock(),
    )
    assert isinstance(result, VerifierResult)
    assert result.satisfied is True
    assert result.new_leads == []


async def test_verifier_unsatisfied_returns_new_leads():
    body = (
        '{"satisfied": false,'
        ' "gaps": ["no GitHub probe done"],'
        ' "new_leads": [{"kind":"github","description":"search github for jane","priority":90}]}'
    )
    fake = BindableFake(responses=[_ai(body)])
    result = await verify(
        subject="Jane",
        report_text="...",
        findings=[],
        leads_log=[],
        llm=fake,
        cost_cb=MagicMock(),
    )
    assert result.satisfied is False
    assert len(result.new_leads) == 1
    assert result.new_leads[0].kind == "github"
    assert result.new_leads[0].priority == 90


async def test_verifier_malformed_json_treated_as_satisfied_on_retry_failure():
    """Per spec: if verifier returns malformed JSON twice, accept the
    draft (better to ship a slightly weaker report than burn budget)."""
    fake = BindableFake(responses=[
        AIMessage(content="totally not json", tool_calls=[]),
        AIMessage(content="still not json", tool_calls=[]),
    ])
    result = await verify(
        subject="Jane",
        report_text="...",
        findings=[],
        leads_log=[],
        llm=fake,
        cost_cb=MagicMock(),
    )
    assert result.satisfied is True
    assert result.new_leads == []
