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
