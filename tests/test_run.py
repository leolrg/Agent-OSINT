from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from osint.run import scan
from osint.types import ScanConfig


class BindableFakeModel(FakeMessagesListChatModel):
    """FakeMessagesListChatModel that supports bind_tools (returns self).

    create_react_agent calls model.bind_tools(...) before running; the base
    FakeMessagesListChatModel raises NotImplementedError, so we override it
    here to simply return self — the fake still returns its canned responses.
    """

    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self


def _ai_final(text: str) -> AIMessage:
    return AIMessage(content=text, tool_calls=[])


FINAL_JSON = (
    '```json\n{"extracted_identifiers":{"emails":["j@e"]},'
    '"report":{"summary":"hi"}}\n```'
)


@pytest.fixture(autouse=True)
def _apify_env(monkeypatch):
    # web_search and web_extract are Apify-backed; APIFY_TOKEN is the auth
    # env var. Stub it so build_tools() doesn't fail when constructing
    # the tool list.
    monkeypatch.setenv("APIFY_TOKEN", "test")


async def test_scan_rejects_empty_subject(tmp_path):
    with pytest.raises(ValueError):
        await scan(subject="   ", config=ScanConfig(), llm=MagicMock(), scans_dir=tmp_path)


async def test_scan_happy_path_no_tool_calls(tmp_path):
    """LLM emits a final-report assistant message immediately, no tool calls."""
    fake = BindableFakeModel(responses=[_ai_final(FINAL_JSON)])
    result = await scan(
        subject="Jane, j@e",
        config=ScanConfig(enabled_tools={"web_search"}),
        llm=fake,
        scans_dir=tmp_path,
    )
    assert result.report == {"summary": "hi"}
    assert result.extracted_identifiers == {"emails": ["j@e"]}
    assert result.path.exists()


async def test_scan_runs_multiple_passes_and_merges_identifiers(tmp_path):
    """passes=2 invokes the agent twice. Each pass produces its own report
    and identifier set; the FINAL state has the latest report's text plus
    the union of both passes' identifiers (so a deepen pass that forgets
    to repeat an earlier email doesn't drop it from the audit trail)."""
    import json

    PASS1_FINAL = (
        "**Executive Summary**\n\nPass 1: Jane is a SWE.\n\n"
        '```json\n{"extracted_identifiers":'
        '{"emails":["jane@old.com"],"usernames":["jdoe"]}}\n```'
    )
    PASS2_FINAL = (
        "**Executive Summary**\n\nPass 2: Jane is a SWE in NYC.\n\n"
        '```json\n{"extracted_identifiers":'
        '{"emails":["jane@new.com"],"name_variations":["Jane D."]}}\n```'
    )
    fake = BindableFakeModel(responses=[
        _ai_final(PASS1_FINAL),
        _ai_final(PASS2_FINAL),
    ])
    result = await scan(
        subject="Jane",
        config=ScanConfig(enabled_tools={"web_search"}, passes=2),
        llm=fake,
        scans_dir=tmp_path,
    )
    # The latest pass's report text wins.
    assert "Pass 2" in result.report.get("text", "")
    # Identifiers are union-merged across passes — neither pass's findings
    # are dropped just because the other forgot to repeat them.
    ids = result.extracted_identifiers
    assert set(ids.get("emails", [])) == {"jane@old.com", "jane@new.com"}
    assert ids.get("usernames") == ["jdoe"]            # from pass 1 only
    assert ids.get("name_variations") == ["Jane D."]   # from pass 2 only

    # Both passes should appear in the message log captured to JSON.
    data = json.loads(result.path.read_text())
    ai_msgs = [m for m in data["messages"] if m["type"] == "ai"]
    assert any("Pass 1" in (m.get("content") or "") for m in ai_msgs)
    assert any("Pass 2" in (m.get("content") or "") for m in ai_msgs)
    # Per-pass audit trail (pass_reports) records BOTH drafts independently,
    # so the historical chain is preserved even though state.report holds
    # only the latest.
    assert "pass_reports" in data
    assert len(data["pass_reports"]) == 2
    assert data["pass_reports"][0]["pass_num"] == 1
    assert "Pass 1" in data["pass_reports"][0]["report"]["text"]
    assert data["pass_reports"][1]["pass_num"] == 2
    assert "Pass 2" in data["pass_reports"][1]["report"]["text"]
    # Each pass entry retains that pass's OWN identifier list (not merged) —
    # state.extracted_identifiers above is the union; pass_reports are raw.
    assert data["pass_reports"][0]["extracted_identifiers"]["emails"] == ["jane@old.com"]
    assert data["pass_reports"][1]["extracted_identifiers"]["emails"] == ["jane@new.com"]


async def test_scan_passes_default_to_one(tmp_path):
    """Back-compat: omitting `passes` runs exactly one pass (no deepen),
    matching the pre-multi-pass behaviour."""
    fake = BindableFakeModel(responses=[_ai_final(FINAL_JSON)])
    result = await scan(
        subject="Jane",
        config=ScanConfig(enabled_tools={"web_search"}),  # no passes=
        llm=fake,
        scans_dir=tmp_path,
    )
    # Only one final-AIMessage in the captured history (pass 1, terminal).
    import json
    data = json.loads(result.path.read_text())
    final_ai_msgs = [
        m for m in data["messages"]
        if m["type"] == "ai"
        and not (m.get("tool_calls") or [])
        and "extracted_identifiers" in (m.get("content") or "")
    ]
    assert len(final_ai_msgs) == 1


async def test_scan_captures_full_message_history(tmp_path):
    """The agent loop's message list (system + human seed + AI replies)
    lands in the scan JSON's `messages` field after a successful run."""
    import json
    fake = BindableFakeModel(responses=[_ai_final(FINAL_JSON)])
    await scan(
        subject="Jane",
        config=ScanConfig(enabled_tools={"web_search"}),
        llm=fake,
        scans_dir=tmp_path,
    )
    # Find the JSON file (only one was written).
    json_files = list(tmp_path.glob("*.json"))
    assert len(json_files) == 1
    data = json.loads(json_files[0].read_text())

    msgs = data["messages"]
    # At minimum: the seed HumanMessage and the AIMessage with the final JSON.
    types = [m["type"] for m in msgs]
    assert "human" in types
    assert "ai" in types
    # The AI's content should include the final-report fenced JSON block.
    ai_msgs = [m for m in msgs if m["type"] == "ai"]
    assert any("extracted_identifiers" in (m.get("content") or "") for m in ai_msgs)


async def test_scan_writes_failed_json_on_unexpected_error(tmp_path, monkeypatch):
    import osint.run as run_module
    monkeypatch.setattr(run_module, "create_react_agent",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        await scan(subject="Jane", config=ScanConfig(enabled_tools={"web_search"}),
                   llm=MagicMock(), scans_dir=tmp_path)
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    import json
    data = json.loads(files[0].read_text())
    assert data["status"] == "failed"


async def test_scan_synthesizes_on_scan_stopped(tmp_path, monkeypatch):
    import osint.run as run_module
    from osint.errors import ScanStopped

    async def raise_stopped(*_a, **_k):
        raise ScanStopped("budget")

    fake_agent = MagicMock()
    fake_agent.ainvoke = AsyncMock(side_effect=raise_stopped)
    monkeypatch.setattr(run_module, "create_react_agent", lambda *a, **k: fake_agent)

    synth_llm = MagicMock()
    synth_llm.ainvoke = AsyncMock(return_value=AIMessage(content=FINAL_JSON))

    result = await scan(
        subject="Jane",
        config=ScanConfig(enabled_tools={"web_search"}),
        llm=synth_llm,
        scans_dir=tmp_path,
    )
    assert result.report == {"summary": "hi"}
    assert synth_llm.ainvoke.await_count == 1


async def test_scan_synthesizes_on_timeout(tmp_path, monkeypatch):
    import osint.run as run_module
    import asyncio

    async def hang(*_a, **_k):
        await asyncio.sleep(10)

    fake_agent = MagicMock()
    fake_agent.ainvoke = AsyncMock(side_effect=hang)
    monkeypatch.setattr(run_module, "create_react_agent", lambda *a, **k: fake_agent)

    synth_llm = MagicMock()
    synth_llm.ainvoke = AsyncMock(return_value=AIMessage(content=FINAL_JSON))

    result = await scan(
        subject="Jane",
        config=ScanConfig(enabled_tools={"web_search"}, max_wall_clock_sec=1),
        llm=synth_llm,
        scans_dir=tmp_path,
    )
    assert result.report == {"summary": "hi"}
