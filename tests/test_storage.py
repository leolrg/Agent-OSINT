import json
from datetime import datetime, timezone
from pathlib import Path

from osint.state import ScanState
from osint.storage import new_scan_id, write_scan_json, write_scan_markdown
from osint.types import ScanConfig, ToolCallRecord


def test_new_scan_id_is_uuidish():
    sid = new_scan_id()
    assert len(sid) >= 32
    assert sid != new_scan_id()


async def test_write_scan_json(tmp_path: Path):
    state = ScanState(scan_id="abc123", subject="Jane", config=ScanConfig())
    now = datetime.now(timezone.utc)
    state.record_tool_call(ToolCallRecord(
        turn=1, tool="tavily_search", tool_call_id="c1",
        input={"q": "x"}, output={"results": []}, raw={"results": []},
        started_at=now, completed_at=now, cost_usd=0.004,
    ))
    state.record_llm_usage(input_tokens=5_000, output_tokens=1_000)
    state.record_final_report({"summary": "done"}, identifiers={"emails": ["j@e"]})

    path = await write_scan_json(tmp_path, state, status="done")

    assert path == tmp_path / "abc123.json"
    data = json.loads(path.read_text())
    assert data["scan_id"] == "abc123"
    assert data["subject"] == "Jane"
    assert data["status"] == "done"
    assert data["extracted_identifiers"] == {"emails": ["j@e"]}
    assert data["report"] == {"summary": "done"}
    assert data["tool_calls"][0]["tool"] == "tavily_search"
    assert data["tool_cost_usd"] == 0.004
    # default pricing: 5_000 * 2 / 1M + 1_000 * 6 / 1M = 0.010 + 0.006 = 0.016
    assert data["llm_cost_usd"] == 0.016
    assert data["llm_input_tokens"] == 5_000
    assert data["llm_output_tokens"] == 1_000
    assert data["total_cost_usd"] == 0.020


async def test_write_scan_json_created_at_before_completed_at(tmp_path: Path):
    state = ScanState(scan_id="t1", subject="Jane", config=ScanConfig())
    # Simulate a scan that took ~2 seconds.
    import time as _time
    state.started_at = _time.monotonic() - 2.0
    state.record_final_report({"summary": "x"})

    path = await write_scan_json(tmp_path, state, status="done")
    data = json.loads(path.read_text())

    from datetime import datetime as _dt
    created_at = _dt.fromisoformat(data["created_at"])
    completed_at = _dt.fromisoformat(data["completed_at"])
    delta = (completed_at - created_at).total_seconds()
    # Should reflect the simulated ~2s elapsed, with a small tolerance.
    assert 1.5 < delta < 2.5
    assert data["duration_sec"] >= 2.0


async def test_write_scan_json_failed_status(tmp_path: Path):
    state = ScanState(scan_id="failed1", subject="Jane", config=ScanConfig())
    path = await write_scan_json(tmp_path, state, status="failed")
    data = json.loads(path.read_text())
    assert data["status"] == "failed"


async def test_write_scan_markdown_renders_prose_report(tmp_path: Path):
    state = ScanState(scan_id="md1", subject="Jane Doe, NYC", config=ScanConfig())
    now = datetime.now(timezone.utc)
    state.record_tool_call(ToolCallRecord(
        turn=1, tool="tavily_search", tool_call_id="c1",
        input={"query": "Jane Doe NYC"}, output={"results": [{"url": "https://x"}]},
        raw={"results": []},
        started_at=now, completed_at=now, cost_usd=0.008,
    ))
    state.record_llm_usage(input_tokens=2_000, output_tokens=500)
    state.record_final_report(
        {"text": "**Executive Summary**\n\nJane Doe is...\n\n**Sources**\n- tavily_search ..."},
        identifiers={"emails": ["jane@example.com"], "urls": ["https://x"]},
    )

    path = await write_scan_markdown(tmp_path, state, status="done")

    assert path == tmp_path / "md1.md"
    md = path.read_text(encoding="utf-8")

    # Header / metadata
    assert "# Scan `md1`" in md
    assert "**Subject:** Jane Doe, NYC" in md
    assert "**Status:** done" in md
    assert "**Tool calls:** 1" in md
    # 2000×$2/M + 500×$6/M = $0.0040 + $0.0030 = $0.0070 LLM
    # plus 0.008 tool = $0.015 total
    assert "**Cost:**" in md and "$0.0150" in md
    assert "2,000 in / 500 out" in md  # token formatting

    # Body: the prose report (markdown headers preserved)
    assert "**Executive Summary**" in md
    assert "**Sources**" in md

    # Identifiers code block
    assert "## Extracted Identifiers" in md
    assert '"emails"' in md
    assert "jane@example.com" in md

    # Tool-call log
    assert "## Tool Call Log" in md
    assert "1. **tavily_search**" in md
    assert "$0.0080" in md


async def test_write_scan_markdown_dumps_old_envelope_report_as_json(tmp_path: Path):
    """If the report dict has no 'text' key (old envelope shape), the body
    section falls back to a JSON code block of the report dict."""
    state = ScanState(scan_id="md2", subject="Jane", config=ScanConfig())
    state.record_final_report({"summary": "hi", "accounts": []})
    path = await write_scan_markdown(tmp_path, state, status="done")
    md = path.read_text(encoding="utf-8")
    assert '"summary": "hi"' in md
    assert "```json" in md


async def test_write_scan_markdown_handles_no_report_no_calls(tmp_path: Path):
    """Defensive: bare ScanState with neither a report nor tool calls."""
    state = ScanState(scan_id="md3", subject="Jane", config=ScanConfig())
    path = await write_scan_markdown(tmp_path, state, status="failed")
    md = path.read_text(encoding="utf-8")
    assert "# Scan `md3`" in md
    assert "**Status:** failed" in md
    assert "_(no report was produced)_" in md
    assert "_(no tool calls were made)_" in md
