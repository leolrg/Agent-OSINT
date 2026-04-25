import json
from datetime import datetime, timezone
from pathlib import Path

from osint.state import ScanState
from osint.storage import new_scan_id, write_scan_json
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
