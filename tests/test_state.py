import time
from datetime import datetime, timezone
import pytest
from osint.state import ScanState, StopReason
from osint.types import ScanConfig, ToolCallRecord


def _tc(cost: float = 0.0) -> ToolCallRecord:
    now = datetime.now(timezone.utc)
    return ToolCallRecord(
        turn=1, tool="t", tool_call_id="x",
        input={}, output={}, raw={},
        started_at=now, completed_at=now, cost_usd=cost,
    )


def test_fresh_state_does_not_stop():
    s = ScanState(scan_id="x", subject="S", config=ScanConfig())
    stop, _ = s.should_stop()
    assert stop is False


def test_stops_on_budget_tool_cost_only():
    s = ScanState(scan_id="x", subject="S", config=ScanConfig(budget_usd=0.05))
    s.record_tool_call(_tc(cost=0.04))
    s.record_tool_call(_tc(cost=0.02))
    stop, reason = s.should_stop()
    assert stop is True
    assert reason == StopReason.BUDGET


def test_stops_on_budget_llm_plus_tool_combined():
    """Budget must count LLM cost together with tool cost, not just tool."""
    s = ScanState(scan_id="x", subject="S", config=ScanConfig(budget_usd=0.10))
    s.record_tool_call(_tc(cost=0.06))     # tool_cost = 0.06
    s.record_llm_usage(input_tokens=20_000, output_tokens=2_000)
    # default pricing: 20_000 * 2 / 1M + 2_000 * 6 / 1M = 0.04 + 0.012 = 0.052
    # combined = 0.06 + 0.052 = 0.112 > 0.10
    assert s.tool_cost_usd == pytest.approx(0.06)
    assert s.llm_cost_usd == pytest.approx(0.052)
    assert s.total_cost_usd == pytest.approx(0.112)
    stop, reason = s.should_stop()
    assert stop is True
    assert reason == StopReason.BUDGET


def test_stops_on_max_tool_calls():
    s = ScanState(scan_id="x", subject="S", config=ScanConfig(max_tool_calls=2))
    s.record_tool_call(_tc())
    s.record_tool_call(_tc())
    stop, reason = s.should_stop()
    assert stop is True
    assert reason == StopReason.MAX_CALLS


def test_stops_on_wall_clock():
    s = ScanState(scan_id="x", subject="S", config=ScanConfig(max_wall_clock_sec=1))
    s.started_at = time.monotonic() - 5
    stop, reason = s.should_stop()
    assert stop is True
    assert reason == StopReason.WALL_CLOCK


def test_record_llm_usage_accumulates():
    s = ScanState(scan_id="x", subject="S", config=ScanConfig())
    s.record_llm_usage(input_tokens=1_000, output_tokens=500)
    s.record_llm_usage(input_tokens=2_500, output_tokens=750)
    assert s.llm_input_tokens == 3_500
    assert s.llm_output_tokens == 1_250


def test_messages_default_empty_and_appendable():
    """messages list defaults to [] and accepts the serialized BaseMessage
    dicts that osint.run._serialize_messages produces."""
    s = ScanState(scan_id="x", subject="S", config=ScanConfig())
    assert s.messages == []
    s.messages.append({"type": "system", "content": "you are an OSINT agent"})
    s.messages.append({"type": "human", "content": "Begin the scan."})
    assert [m["type"] for m in s.messages] == ["system", "human"]


def test_final_report_tracking():
    s = ScanState(scan_id="x", subject="S", config=ScanConfig())
    assert s.has_final_report() is False
    s.record_final_report({"summary": "hi"}, identifiers={"emails": []})
    assert s.has_final_report() is True
    assert s.report == {"summary": "hi"}


def test_scanstate_v2_fields_default_empty():
    """v2-only fields default to empty containers so v1 scans serialize
    unchanged shape (the fields are present but empty)."""
    from osint.state import ScanState
    from osint.types import ScanConfig
    s = ScanState(scan_id="x", subject="Jane", config=ScanConfig())
    assert s.findings == []
    assert s.leads_log == []
    assert s.verifier_iterations == 0
