from osint.agents.critic_react_v3.critic import Verdict, parse_critic_verdict


def test_accept_verdict():
    v = parse_critic_verdict("VERDICT: ACCEPT\n")
    assert v.accept is True
    assert v.gaps == []


def test_reject_with_bullets():
    text = (
        "VERDICT: REJECT\n"
        "GAPS:\n"
        "- Subject's current employer not confirmed\n"
        "- Email fc202817@bunka-fc.ac.jp never followed up via web_search\n"
    )
    v = parse_critic_verdict(text)
    assert v.accept is False
    assert v.gaps == [
        "Subject's current employer not confirmed",
        "Email fc202817@bunka-fc.ac.jp never followed up via web_search",
    ]


def test_reject_without_bullets_still_rejected_but_empty_gaps():
    v = parse_critic_verdict("VERDICT: REJECT\n")
    assert v.accept is False
    assert v.gaps == []


def test_malformed_treated_as_accept():
    v = parse_critic_verdict("nonsense, no verdict line at all")
    assert v.accept is True
    assert v.gaps == []


def test_verdict_case_insensitive():
    assert parse_critic_verdict("verdict: accept").accept is True
    assert parse_critic_verdict("Verdict: Reject\nGAPS:\n- X").accept is False


def test_reject_with_mixed_bullet_styles():
    text = (
        "VERDICT: REJECT\n"
        "GAPS:\n"
        "* alpha\n"
        "1. beta\n"
        "- gamma\n"
        "• delta\n"
        "2) epsilon\n"
    )
    v = parse_critic_verdict(text)
    assert v.accept is False
    assert v.gaps == ["alpha", "beta", "gamma", "delta", "epsilon"]


def test_reject_stops_collecting_at_next_header():
    text = (
        "VERDICT: REJECT\n"
        "GAPS:\n"
        "- real gap\n"
        "\n"
        "NOTES:\n"
        "- not a gap\n"
    )
    v = parse_critic_verdict(text)
    assert v.accept is False
    assert v.gaps == ["real gap"]


from unittest.mock import MagicMock

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from osint.agents.critic_react_v3.critic import critic


async def test_critic_returns_accept_verdict():
    fake = FakeMessagesListChatModel(responses=[AIMessage(content="VERDICT: ACCEPT\n")])
    v = await critic(
        subject="Jane",
        goal="coffee chat about ML",
        preset="coffee_career",
        draft="Jane works at Acme on ML infra...",
        tool_calls=[],
        llm=fake,
        cost_cb=MagicMock(),
    )
    assert v.accept is True
    assert v.gaps == []


async def test_critic_returns_reject_with_gaps():
    fake = FakeMessagesListChatModel(responses=[AIMessage(
        content="VERDICT: REJECT\nGAPS:\n- No current role\n- Email never probed\n"
    )])
    v = await critic(
        subject="Jane",
        goal="",
        preset="dossier",
        draft="Jane lives in Tokyo.",
        tool_calls=[],
        llm=fake,
        cost_cb=MagicMock(),
    )
    assert v.accept is False
    assert v.gaps == ["No current role", "Email never probed"]


from osint.agents.critic_react_v3.critic import _summarize_tool_calls
from osint.types import ToolCallRecord
from datetime import datetime, timezone


def _toolcall_record(tool: str, turn: int = 1) -> ToolCallRecord:
    """Minimal ToolCallRecord matching the runner's call-site shape."""
    now = datetime.now(timezone.utc)
    return ToolCallRecord(
        turn=turn,
        tool=tool,
        input={},
        output=None,
        raw=None,
        started_at=now,
        completed_at=now,
        cost_usd=0.0,
    )


def test_summarize_tool_calls_empty_returns_placeholder():
    assert _summarize_tool_calls([]) == "(no tool calls were made)"


def test_summarize_tool_calls_pydantic_records():
    """Real call-site shape: list[ToolCallRecord] with .tool attribute."""
    calls = [
        _toolcall_record("web_search"),
        _toolcall_record("web_search", turn=2),
        _toolcall_record("apify_instagram", turn=3),
    ]
    summary = _summarize_tool_calls(calls)
    assert summary == "apify_instagram=1, web_search=2"


def test_summarize_tool_calls_dict_fallback():
    """Dict-shaped entries are tolerated as a defensive fallback."""
    calls = [{"tool": "web_search"}, {"tool": "web_search"}, {"tool": "maigret"}]
    summary = _summarize_tool_calls(calls)
    assert summary == "maigret=1, web_search=2"


def test_summarize_tool_calls_unknown_when_neither():
    """Items without a .tool attribute or 'tool' key fall through to 'unknown'."""
    calls = [object(), object()]
    summary = _summarize_tool_calls(calls)
    assert summary == "unknown=2"
