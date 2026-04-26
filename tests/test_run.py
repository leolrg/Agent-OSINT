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
def _tavily_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test")


async def test_scan_rejects_empty_subject(tmp_path):
    with pytest.raises(ValueError):
        await scan(subject="   ", config=ScanConfig(), llm=MagicMock(), scans_dir=tmp_path)


async def test_scan_happy_path_no_tool_calls(tmp_path):
    """LLM emits a final-report assistant message immediately, no tool calls."""
    fake = BindableFakeModel(responses=[_ai_final(FINAL_JSON)])
    result = await scan(
        subject="Jane, j@e",
        config=ScanConfig(enabled_tools={"tavily_search"}),
        llm=fake,
        scans_dir=tmp_path,
    )
    assert result.report == {"summary": "hi"}
    assert result.extracted_identifiers == {"emails": ["j@e"]}
    assert result.path.exists()


async def test_scan_captures_full_message_history(tmp_path):
    """The agent loop's message list (system + human seed + AI replies)
    lands in the scan JSON's `messages` field after a successful run."""
    import json
    fake = BindableFakeModel(responses=[_ai_final(FINAL_JSON)])
    await scan(
        subject="Jane",
        config=ScanConfig(enabled_tools={"tavily_search"}),
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
        await scan(subject="Jane", config=ScanConfig(enabled_tools={"tavily_search"}),
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
        config=ScanConfig(enabled_tools={"tavily_search"}),
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
        config=ScanConfig(enabled_tools={"tavily_search"}, max_wall_clock_sec=1),
        llm=synth_llm,
        scans_dir=tmp_path,
    )
    assert result.report == {"summary": "hi"}
