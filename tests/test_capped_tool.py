from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from osint.capped_tool import CappedTool
from osint.errors import ScanStopped
from osint.state import ScanState
from osint.types import ScanConfig, ToolCallRecord


class _EchoInput(BaseModel):
    q: str


class _Echo(BaseTool):
    name: str = "echo"
    description: str = "echoes"
    args_schema: type = _EchoInput
    response_format: str = "content_and_artifact"

    def _run(self, q: str):
        raise NotImplementedError

    async def _arun(self, q: str) -> tuple[str, dict]:
        return f"echo:{q}", {"echoed": q, "raw": {"q": q}}


class _Plain(BaseTool):
    name: str = "plain"
    description: str = "plain string out"
    args_schema: type = _EchoInput

    def _run(self, q: str):
        raise NotImplementedError

    async def _arun(self, q: str) -> str:
        return f"plain:{q}"


async def test_capped_tool_records_artifact_as_raw():
    state = ScanState(scan_id="s", subject="S", config=ScanConfig())
    capped = CappedTool(wrapped=_Echo(), state=state, est_cost_usd=0.01)
    out = await capped.ainvoke({"q": "hi", "_tool_call_id": "call_1"})
    # When response_format is content_and_artifact, ainvoke returns the
    # content string (LangChain unwraps the artifact internally).
    assert out == "echo:hi"
    assert len(state.tool_calls) == 1
    rec = state.tool_calls[0]
    assert rec.tool == "echo"
    assert rec.tool_call_id == "call_1"
    assert rec.output == {"echoed": "hi", "raw": {"q": "hi"}}
    assert rec.raw == {"echoed": "hi", "raw": {"q": "hi"}}
    assert rec.cost_usd == 0.01
    assert rec.error is None


async def test_capped_tool_records_plain_string_output():
    state = ScanState(scan_id="s", subject="S", config=ScanConfig())
    capped = CappedTool(wrapped=_Plain(), state=state, est_cost_usd=0.0)
    out = await capped.ainvoke({"q": "hi", "_tool_call_id": "c"})
    assert out == "plain:hi"
    rec = state.tool_calls[0]
    assert rec.output == {"text": "plain:hi"}
    assert rec.raw == "plain:hi"


async def test_capped_tool_raises_when_stopped():
    state = ScanState(scan_id="s", subject="S", config=ScanConfig(budget_usd=0.01))
    # inflate cost so state is already over budget
    now = datetime.now(timezone.utc)
    state.record_tool_call(ToolCallRecord(
        turn=0, tool="prev", tool_call_id=None,
        input={}, output={}, raw={},
        started_at=now, completed_at=now, cost_usd=0.02,
    ))
    capped = CappedTool(wrapped=_Echo(), state=state, est_cost_usd=0.01)
    with pytest.raises(ScanStopped) as exc:
        await capped.ainvoke({"q": "x", "_tool_call_id": "c"})
    assert exc.value.reason == "budget"


async def test_capped_tool_logs_inner_exception_and_reraises():
    class _Boom(BaseTool):
        name: str = "boom"
        description: str = "raises"
        args_schema: type = _EchoInput

        def _run(self, q: str):
            raise NotImplementedError

        async def _arun(self, q: str) -> str:
            raise RuntimeError("nope")

    state = ScanState(scan_id="s", subject="S", config=ScanConfig())
    capped = CappedTool(wrapped=_Boom(), state=state, est_cost_usd=0.0)
    with pytest.raises(RuntimeError):
        await capped.ainvoke({"q": "x", "_tool_call_id": "c"})
    rec = state.tool_calls[0]
    assert "nope" in rec.error
    assert rec.output is None
