# Self-OSINT v1 Agent — Implementation Plan (LangGraph)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python library exposing `async def scan(subject: str, config) -> ScanResult` that drives a Grok-4.20 ReAct agent (LangGraph's `create_react_agent`) over six OSINT tools (`tavily_search`, `tavily_extract`, `maigret`, `apify_instagram`, `apify_linkedin`, `apify_twitter`) and writes a JSON-per-scan record with verbatim tool output.

**Architecture:** One Python package (`osint/`). The agent loop is LangGraph's prebuilt ReAct agent; we wire Grok in as a `ChatOpenAI` pointed at xAI's OpenAI-compatible endpoint. Tavily tools are imported from `langchain-tavily` unchanged. Custom tools (Maigret, Apify IG/LinkedIn/Twitter) are `langchain_core.tools.BaseTool` subclasses. Every tool is wrapped in a `CappedTool` that enforces per-scan budget/call/time caps and records each invocation (with the raw vendor response) to `ScanState`. After the agent loop terminates (normal, cap hit, or recursion limit), the final LLM message is parsed into a structured report and written as a single JSON file.

**Tech Stack:** Python 3.11+, Pydantic v2, `langgraph`, `langchain-core`, `langchain-openai` (ChatOpenAI→xAI Chat Completions for the main agent), `langchain-tavily`, `apify-client`, `maigret` (library), `structlog`. Tests use `pytest`, `pytest-asyncio`, `pytest-mock`, and `langchain_core.language_models.fake_chat_models.FakeMessagesListChatModel` for scripted LLM responses.

**Spec reference:** `docs/superpowers/specs/2026-04-24-self-osint-backend-design.md`

---

## File Structure

```
osint/
├── __init__.py          # public exports
├── types.py             # Pydantic: ScanConfig, ToolCallRecord, ScanResult
├── state.py             # ScanState + StopReason
├── storage.py           # write_scan_json, new_scan_id
├── errors.py            # ScanConfigError, ScanStopped
├── prompts.py           # build_system_prompt, build_synthesis_prompt, parse_report
├── capped_tool.py       # CappedTool wrapper — enforces caps, logs to state
├── llm_cost.py          # LLMCostCallback — records LLM token usage into ScanState
├── log.py               # structlog setup
├── scan.py              # scan() using LangGraph's create_react_agent
├── cli.py               # argparse CLI
└── tools/
    ├── __init__.py      # build_tools(config, state) factory
    ├── tavily.py        # thin wrappers around langchain-tavily
    ├── maigret.py       # BaseTool subclass, direct-scraping
    └── apify.py         # BaseTool subclasses for IG + LinkedIn + Twitter

tests/
├── __init__.py
├── conftest.py
├── test_types.py
├── test_state.py
├── test_storage.py
├── test_capped_tool.py
├── test_llm_cost.py
├── test_tools_tavily.py
├── test_tools_maigret.py
├── test_tools_apify.py
├── test_prompts.py
├── test_scan.py
└── test_cli.py
```

LangGraph's prebuilt ReAct agent replaces what would otherwise be a custom agent loop — that's why there's no `osint/llm.py` or custom `Tool Protocol`/`invoke_tool`. Our value-add over bare LangGraph is `CappedTool` (budget + state-log side-channel) plus the custom tools.

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `osint/__init__.py`
- Create: `osint/tools/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `.gitignore`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "osint"
version = "0.1.0"
description = "Self-OSINT backend — v1 LangGraph agent"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.5",
    "langgraph>=0.2.60",
    "langchain-core>=0.3.20",
    "langchain-openai>=0.2.10",
    "langchain-tavily>=0.1.0",
    "apify-client>=1.7",
    "maigret>=0.4.4",
    "structlog>=24.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-mock>=3.12",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["osint*"]
```

- [ ] **Step 2: Create empty package and test files**

```bash
mkdir -p osint/tools tests
touch osint/__init__.py osint/tools/__init__.py tests/__init__.py tests/conftest.py
```

- [ ] **Step 3: Write `.gitignore`**

```
__pycache__/
*.pyc
*.egg-info/
.pytest_cache/
.venv/
venv/
scans/
.env
```

- [ ] **Step 4: Install and verify**

Run: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
Run: `pytest -q`
Expected: `no tests ran`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore osint/ tests/
git commit -m "chore: scaffold osint package with langgraph deps"
```

---

## Task 2: Config types (`ScanConfig`)

**Files:**
- Create: `osint/types.py`
- Create: `tests/test_types.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_types.py`:

```python
from osint.types import LLMPricing, ScanConfig


def test_scanconfig_defaults():
    c = ScanConfig()
    assert c.enabled_tools == {"tavily_search", "tavily_extract", "maigret"}
    assert c.budget_usd == 5.0
    assert c.max_tool_calls == 30
    assert c.max_wall_clock_sec == 600
    assert c.tool_concurrency == {"maigret": 2}
    assert c.tool_options == {}
    # grok-4.20 public pricing as of 2026-04 (xAI docs).
    assert c.llm_pricing.input_per_mtok_usd == 2.0
    assert c.llm_pricing.output_per_mtok_usd == 6.0


def test_scanconfig_rejects_nonpositive_caps():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ScanConfig(budget_usd=0)
    with pytest.raises(ValidationError):
        ScanConfig(max_tool_calls=0)
    with pytest.raises(ValidationError):
        ScanConfig(max_wall_clock_sec=0)


def test_scanconfig_overrides():
    c = ScanConfig(
        enabled_tools={"tavily_search"},
        budget_usd=1.0,
        tool_options={"maigret": {"proxy_url": "http://p:8080"}},
        llm_pricing=LLMPricing(input_per_mtok_usd=3.0, output_per_mtok_usd=15.0),
    )
    assert c.enabled_tools == {"tavily_search"}
    assert c.tool_options["maigret"]["proxy_url"] == "http://p:8080"
    assert c.llm_pricing.input_per_mtok_usd == 3.0
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/test_types.py -v`
Expected: `ModuleNotFoundError: No module named 'osint.types'`.

- [ ] **Step 3: Implement `ScanConfig`**

Create `osint/types.py`:

```python
from pydantic import BaseModel, Field, NonNegativeFloat, PositiveFloat, PositiveInt


def default_enabled_tools() -> set[str]:
    return {"tavily_search", "tavily_extract", "maigret"}


def default_tool_concurrency() -> dict[str, int]:
    return {"maigret": 2}


class LLMPricing(BaseModel):
    """Per-million-token pricing used to convert usage_metadata into USD."""
    input_per_mtok_usd: NonNegativeFloat = 2.0   # grok-4.20 default, 2026-04 per xAI docs
    output_per_mtok_usd: NonNegativeFloat = 6.0


class ScanConfig(BaseModel):
    enabled_tools: set[str] = Field(default_factory=default_enabled_tools)
    budget_usd: PositiveFloat = 5.0
    max_tool_calls: PositiveInt = 30
    max_wall_clock_sec: PositiveInt = 600
    tool_concurrency: dict[str, int] = Field(default_factory=default_tool_concurrency)
    tool_options: dict[str, dict] = Field(default_factory=dict)
    llm_pricing: LLMPricing = Field(default_factory=LLMPricing)
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_types.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/types.py tests/test_types.py
git commit -m "feat(types): add ScanConfig with validated caps"
```

---

## Task 3: Runtime types (`ToolCallRecord`, `ScanResult`)

**Files:**
- Modify: `osint/types.py`
- Modify: `tests/test_types.py`

- [ ] **Step 1: Extend tests**

Append to `tests/test_types.py`:

```python
from datetime import datetime
from pathlib import Path
from osint.types import ToolCallRecord, ScanResult


def test_toolcallrecord_defaults():
    now = datetime(2026, 4, 24)
    tc = ToolCallRecord(
        turn=1, tool="tavily_search", tool_call_id="call_a",
        input={"query": "x"}, output={"results": []}, raw={"results": []},
        started_at=now, completed_at=now, cost_usd=0.004,
    )
    assert tc.error is None


def test_scanresult_fields():
    s = ScanResult(
        scan_id="s1", subject="Jane Doe",
        extracted_identifiers={"emails": ["j@e"]},
        report={"summary": "..."},
        tool_calls=[], total_cost_usd=0.0, duration_sec=1.0,
        path=Path("/tmp/s1.json"),
    )
    assert s.subject == "Jane Doe"
    assert s.path.name == "s1.json"
```

- [ ] **Step 2: Run — expect 2 failures**

Run: `pytest tests/test_types.py -v`
Expected: 3 pass, 2 fail with `ImportError` on new symbols.

- [ ] **Step 3: Implement the new types**

Append to `osint/types.py`:

```python
from datetime import datetime
from pathlib import Path
from typing import Any


class ToolCallRecord(BaseModel):
    turn: int
    tool: str
    tool_call_id: str | None = None    # matches LangGraph's tool_calls[].id
    input: dict[str, Any]
    output: dict[str, Any] | None
    raw: Any
    started_at: datetime
    completed_at: datetime
    cost_usd: float
    error: str | None = None


class ScanResult(BaseModel):
    scan_id: str
    subject: str
    extracted_identifiers: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    duration_sec: float = 0.0
    path: Path
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_types.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/types.py tests/test_types.py
git commit -m "feat(types): add ToolCallRecord and ScanResult"
```

---

## Task 4: `ScanState` and stop conditions

**Files:**
- Create: `osint/errors.py`
- Create: `osint/state.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_state.py`:

```python
import time
from datetime import datetime, timezone
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


def test_final_report_tracking():
    s = ScanState(scan_id="x", subject="S", config=ScanConfig())
    assert s.has_final_report() is False
    s.record_final_report({"summary": "hi"}, identifiers={"emails": []})
    assert s.has_final_report() is True
    assert s.report == {"summary": "hi"}
```

Add `import pytest` at the top of `tests/test_state.py`.

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_state.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement `osint/errors.py`**

```python
class ScanConfigError(Exception):
    """Invalid or incomplete scan configuration (e.g. missing API key)."""


class ScanStopped(Exception):
    """Raised when a scan hits a cap mid-flight; caught in scan() for synthesis."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
```

- [ ] **Step 4: Implement `osint/state.py`**

```python
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from osint.types import ScanConfig, ToolCallRecord


class StopReason(str, Enum):
    NONE = "none"
    BUDGET = "budget"
    MAX_CALLS = "max_calls"
    WALL_CLOCK = "wall_clock"
    FINAL_REPORT = "final_report"


@dataclass
class ScanState:
    scan_id: str
    subject: str
    config: ScanConfig
    started_at: float = field(default_factory=time.monotonic)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)
    extracted_identifiers: dict[str, Any] = field(default_factory=dict)
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    _has_report: bool = False

    @property
    def tool_cost_usd(self) -> float:
        return sum(tc.cost_usd for tc in self.tool_calls)

    @property
    def llm_cost_usd(self) -> float:
        p = self.config.llm_pricing
        return (
            self.llm_input_tokens * p.input_per_mtok_usd / 1_000_000
            + self.llm_output_tokens * p.output_per_mtok_usd / 1_000_000
        )

    @property
    def total_cost_usd(self) -> float:
        return self.tool_cost_usd + self.llm_cost_usd

    @property
    def wall_clock_elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def should_stop(self) -> tuple[bool, StopReason]:
        if self.total_cost_usd >= self.config.budget_usd:
            return True, StopReason.BUDGET
        if len(self.tool_calls) >= self.config.max_tool_calls:
            return True, StopReason.MAX_CALLS
        if self.wall_clock_elapsed >= self.config.max_wall_clock_sec:
            return True, StopReason.WALL_CLOCK
        return False, StopReason.NONE

    def record_tool_call(self, tc: ToolCallRecord) -> None:
        self.tool_calls.append(tc)

    def record_llm_usage(self, *, input_tokens: int, output_tokens: int) -> None:
        self.llm_input_tokens += max(0, int(input_tokens or 0))
        self.llm_output_tokens += max(0, int(output_tokens or 0))

    def record_final_report(self, report: dict[str, Any], identifiers: dict[str, Any] | None = None) -> None:
        self.report = report
        if identifiers is not None:
            self.extracted_identifiers = identifiers
        self._has_report = True

    def has_final_report(self) -> bool:
        return self._has_report
```

- [ ] **Step 5: Run tests — expect pass**

Run: `pytest tests/test_state.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add osint/state.py osint/errors.py tests/test_state.py
git commit -m "feat(state): add ScanState with budget/call/wall-clock stop conditions"
```

---

## Task 5: Storage — JSON-per-scan writer

**Files:**
- Create: `osint/storage.py`
- Create: `tests/test_storage.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_storage.py`:

```python
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
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_storage.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement storage**

Create `osint/storage.py`:

```python
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from osint.state import ScanState


def new_scan_id() -> str:
    return uuid.uuid4().hex


async def write_scan_json(
    scans_dir: Path,
    state: ScanState,
    status: Literal["done", "failed"],
) -> Path:
    scans_dir.mkdir(parents=True, exist_ok=True)
    path = scans_dir / f"{state.scan_id}.json"
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "scan_id": state.scan_id,
        "created_at": now,
        "completed_at": now,
        "status": status,
        "subject": state.subject,
        "extracted_identifiers": state.extracted_identifiers,
        "config": state.config.model_dump(mode="json"),
        "tool_calls": [tc.model_dump(mode="json") for tc in state.tool_calls],
        "report": state.report,
        "tool_cost_usd": state.tool_cost_usd,
        "llm_cost_usd": state.llm_cost_usd,
        "llm_input_tokens": state.llm_input_tokens,
        "llm_output_tokens": state.llm_output_tokens,
        "total_cost_usd": state.total_cost_usd,
        "duration_sec": state.wall_clock_elapsed,
    }
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path
```

- [ ] **Step 4: Run — expect pass**

Run: `pytest tests/test_storage.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/storage.py tests/test_storage.py
git commit -m "feat(storage): add JSON-per-scan writer"
```

---

## Task 6: `CappedTool` — cap enforcement + state logging wrapper

**Files:**
- Create: `osint/capped_tool.py`
- Create: `tests/test_capped_tool.py`

`CappedTool` is a `BaseTool` subclass that wraps any other `BaseTool`. On each invocation it:

1. Checks `state.should_stop()` first. If stopped, raises `ScanStopped` (propagates out of the agent loop for synthesis).
2. Times the inner tool's `_arun`. On success, records a `ToolCallRecord` with cost and the raw response.
3. On inner-tool exception, records an error `ToolCallRecord` and re-raises so LangGraph turns it into a tool-error message the LLM can react to.

Tools that want to preserve a structured "raw" distinct from the LLM-visible string use LangChain's `response_format="content_and_artifact"` — their `_arun` returns `(content_str, artifact)`. `CappedTool` stores `artifact` in the record's `raw`; the LLM only sees `content_str`. Tools that return a plain string use that string as both `output` and `raw`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_capped_tool.py`:

```python
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

    async def _arun(self, q: str) -> tuple[str, dict]:
        return f"echo:{q}", {"echoed": q, "raw": {"q": q}}


class _Plain(BaseTool):
    name: str = "plain"
    description: str = "plain string out"
    args_schema: type = _EchoInput

    async def _arun(self, q: str) -> str:
        return f"plain:{q}"


async def test_capped_tool_records_artifact_as_raw():
    state = ScanState(scan_id="s", subject="S", config=ScanConfig())
    capped = CappedTool(wrapped=_Echo(), state=state, est_cost_usd=0.01)
    out = await capped.ainvoke({"q": "hi", "_tool_call_id": "call_1"})
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

        async def _arun(self, q: str) -> str:
            raise RuntimeError("nope")

    state = ScanState(scan_id="s", subject="S", config=ScanConfig())
    capped = CappedTool(wrapped=_Boom(), state=state, est_cost_usd=0.0)
    with pytest.raises(RuntimeError):
        await capped.ainvoke({"q": "x", "_tool_call_id": "c"})
    rec = state.tool_calls[0]
    assert "nope" in rec.error
    assert rec.output is None
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_capped_tool.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement `osint/capped_tool.py`**

```python
from datetime import datetime, timezone
from typing import Any, Type

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, PrivateAttr

from osint.errors import ScanStopped
from osint.state import ScanState
from osint.types import ToolCallRecord


class CappedTool(BaseTool):
    """Wraps a LangChain BaseTool: enforces per-scan caps and records every
    invocation to ScanState (including the raw vendor response via the
    `response_format="content_and_artifact"` convention).
    """

    name: str
    description: str
    args_schema: Type[BaseModel] | None = None
    response_format: str = "content"

    _wrapped: BaseTool = PrivateAttr()
    _state: ScanState = PrivateAttr()
    _est_cost_usd: float = PrivateAttr()

    def __init__(self, wrapped: BaseTool, state: ScanState, est_cost_usd: float):
        super().__init__(
            name=wrapped.name,
            description=wrapped.description,
            args_schema=wrapped.args_schema,
            response_format=getattr(wrapped, "response_format", "content"),
        )
        self._wrapped = wrapped
        self._state = state
        self._est_cost_usd = est_cost_usd

    def _run(self, *args, **kwargs):
        raise NotImplementedError("CappedTool is async-only; use ainvoke().")

    async def _arun(
        self,
        *args,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
        **kwargs,
    ):
        # LangChain injects `_tool_call_id` in the invocation dict when the
        # caller passes it; fall back to None if the tool is called directly.
        tool_call_id = kwargs.pop("_tool_call_id", None)

        stopped, reason = self._state.should_stop()
        if stopped:
            raise ScanStopped(reason.value)

        started = datetime.now(timezone.utc)
        content: Any = None
        artifact: Any = None
        error: str | None = None

        try:
            result = await self._wrapped._arun(*args, run_manager=run_manager, **kwargs)
            if self.response_format == "content_and_artifact" and isinstance(result, tuple):
                content, artifact = result
            else:
                content = result
                artifact = result
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            completed = datetime.now(timezone.utc)
            self._record(started, completed, tool_call_id, kwargs, None, None, error)
            raise

        completed = datetime.now(timezone.utc)
        output_dict = artifact if isinstance(artifact, dict) else {"text": str(content)}
        self._record(started, completed, tool_call_id, kwargs, output_dict, artifact, None)

        if self.response_format == "content_and_artifact":
            return content, artifact
        return content

    def _record(
        self,
        started: datetime,
        completed: datetime,
        tool_call_id: str | None,
        inputs: dict,
        output: dict | None,
        raw: Any,
        error: str | None,
    ) -> None:
        turn = len(self._state.tool_calls) + 1
        self._state.record_tool_call(ToolCallRecord(
            turn=turn,
            tool=self._wrapped.name,
            tool_call_id=tool_call_id,
            input=inputs,
            output=output,
            raw=raw,
            started_at=started,
            completed_at=completed,
            cost_usd=self._est_cost_usd,
            error=error,
        ))
```

> Note on the `_tool_call_id` parameter: LangChain's `BaseTool.ainvoke` accepts a dict that may include reserved keys consumed by LangChain; custom keys starting with `_` are stripped before reaching `_arun`. In LangGraph's prebuilt ReAct agent the `tool_call_id` is populated automatically via `InjectedToolCallId` when a tool parameter is typed that way. To keep this simple for v1 and avoid every inner tool needing `InjectedToolCallId`, the `CappedTool` reads `tool_call_id` from the LangChain callback context inside its `_arun` when present. If the runtime API of `run_manager` proves unreliable, we fall back to using the record's auto-assigned `turn` as the join key in the JSON file.

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_capped_tool.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/capped_tool.py tests/test_capped_tool.py
git commit -m "feat(capped_tool): add BaseTool wrapper enforcing caps and logging to state"
```

---

## Task 7: Maigret tool (direct-scraping BaseTool)

**Files:**
- Create: `osint/tools/maigret.py`
- Create: `tests/test_tools_maigret.py`

Maigret is a LangChain `BaseTool` subclass returning `(content_str, artifact)` so we keep the raw per-site response in the scan log while only sending the LLM a trimmed summary. Process-wide concurrency (the "politeness" cap) is a module-level `asyncio.Semaphore` owned by the tool class.

- [ ] **Step 1: Write failing tests**

Create `tests/test_tools_maigret.py`:

```python
from unittest.mock import AsyncMock

import pytest

from osint.tools.maigret import MaigretTool


async def test_maigret_calls_search_with_defaults(mocker):
    fake_search = AsyncMock(return_value={
        "GitHub": {"status": {"message": "Claimed"}, "url_user": "https://github.com/j"},
        "NotFound": {"status": {"message": "Available"}, "url_user": ""},
    })
    mocker.patch("osint.tools.maigret._search", fake_search)
    tool = MaigretTool()
    content, artifact = await tool._arun(username="jdoe")
    kwargs = fake_search.call_args.kwargs
    assert kwargs["username"] == "jdoe"
    assert kwargs["max_connections"] == 15
    assert kwargs["timeout"] == 10
    assert kwargs.get("proxy") is None
    assert "GitHub" in content
    assert artifact["found_accounts"][0]["site"] == "GitHub"
    assert "raw" in artifact


async def test_maigret_forwards_overrides(mocker):
    fake_search = AsyncMock(return_value={})
    mocker.patch("osint.tools.maigret._search", fake_search)
    tool = MaigretTool(proxy_url="http://p:8080")
    await tool._arun(username="jdoe", max_connections=5, sites_filter=["GitHub"])
    kwargs = fake_search.call_args.kwargs
    assert kwargs["max_connections"] == 5
    assert kwargs["proxy"] == "http://p:8080"
    assert kwargs["site_list"] == ["GitHub"]


async def test_maigret_metadata():
    tool = MaigretTool()
    assert tool.name == "maigret"
    assert tool.response_format == "content_and_artifact"
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_tools_maigret.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement `osint/tools/maigret.py`**

```python
import asyncio
import json
from typing import Any, Type

import maigret as _maigret_pkg
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


# Process-wide politeness cap. See spec §6.6.
_MAIGRET_SEMAPHORE = asyncio.Semaphore(2)

# Resolve the entrypoint at import time. If the maigret release on PyPI ever
# moves it, the package must be pinned to a known-good version in pyproject.toml.
_MAIGRET_SEARCH = getattr(_maigret_pkg, "search", None)


class MaigretInput(BaseModel):
    username: str = Field(description="The username to search for.")
    max_connections: int = Field(default=15, ge=1, le=50)
    timeout: int = Field(default=10, ge=1, le=30)
    sites_filter: list[str] | None = Field(
        default=None,
        description="Restrict the check to these site names.",
    )


async def _search(
    *,
    username: str,
    max_connections: int,
    timeout: int,
    proxy: str | None,
    site_list: list[str] | None,
) -> dict:
    def _run() -> dict:
        return _MAIGRET_SEARCH(
            username=username,
            max_connections=max_connections,
            timeout=timeout,
            proxy=proxy,
            site_list=site_list,
        )

    return await asyncio.to_thread(_run)


class MaigretTool(BaseTool):
    name: str = "maigret"
    description: str = (
        "Check ~3000 websites for the presence of a username and return the "
        "sites where the account exists. Use after you have a confirmed or "
        "likely username. Pass `sites_filter` to restrict the fan-out."
    )
    args_schema: Type[BaseModel] = MaigretInput
    response_format: str = "content_and_artifact"

    proxy_url: str | None = None

    def _run(self, *args, **kwargs):
        raise NotImplementedError("Use async invocation.")

    async def _arun(
        self,
        username: str,
        max_connections: int = 15,
        timeout: int = 10,
        sites_filter: list[str] | None = None,
        **_: Any,
    ) -> tuple[str, dict]:
        async with _MAIGRET_SEMAPHORE:
            raw = await _search(
                username=username,
                max_connections=max_connections,
                timeout=timeout,
                proxy=self.proxy_url,
                site_list=sites_filter,
            )

        found = [
            {"site": site, "url": info.get("url_user"), "status": info.get("status", {}).get("message")}
            for site, info in (raw or {}).items()
            if info.get("status", {}).get("message") in {"Claimed", "Found"}
        ]
        artifact = {"found_accounts": found, "raw": raw}
        content = json.dumps({"found_accounts": found}, default=str)
        return content, artifact
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_tools_maigret.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/tools/maigret.py tests/test_tools_maigret.py
git commit -m "feat(tools): add Maigret as LangChain BaseTool with process-wide semaphore"
```

---

## Task 8: Apify tools (Instagram + LinkedIn + Twitter BaseTools)

**Files:**
- Create: `osint/tools/apify.py`
- Create: `tests/test_tools_apify.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_tools_apify.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from osint.tools.apify import (
    ApifyInstagramTool,
    ApifyLinkedInTool,
    ApifyTwitterTool,
)


def _fake_client(items):
    client = MagicMock()
    actor = MagicMock()
    dataset = MagicMock()
    client.actor = MagicMock(return_value=actor)
    client.dataset = MagicMock(return_value=dataset)
    actor.call = AsyncMock(return_value={"defaultDatasetId": "ds1"})
    dataset.list_items = AsyncMock(return_value=MagicMock(items=items))
    return client, actor, dataset


async def test_apify_instagram_runs_actor():
    client, actor, dataset = _fake_client([{"username": "jdoe"}])
    tool = ApifyInstagramTool(client=client, actor_id="apify~instagram-scraper")
    content, artifact = await tool._arun(username="jdoe")
    actor.call.assert_awaited_once()
    assert artifact["items"][0]["username"] == "jdoe"
    assert "jdoe" in content


async def test_apify_linkedin_runs_actor():
    client, actor, dataset = _fake_client([{"fullName": "Jane"}])
    tool = ApifyLinkedInTool(client=client, actor_id="apify~linkedin-profile-scraper")
    content, artifact = await tool._arun(profile_url="https://www.linkedin.com/in/jane/")
    run_input = actor.call.call_args.kwargs["run_input"]
    assert any("linkedin.com/in/jane" in str(v) for v in run_input.values())
    assert artifact["items"][0]["fullName"] == "Jane"


async def test_apify_twitter_handle_mode_runs_actor():
    client, actor, dataset = _fake_client([{"author": {"userName": "jdoe"}, "text": "hi"}])
    tool = ApifyTwitterTool(client=client, actor_id="apidojo~twitter-scraper-lite")
    content, artifact = await tool._arun(handle="jdoe", max_items=25)
    run_input = actor.call.call_args.kwargs["run_input"]
    assert run_input["twitterHandles"] == ["jdoe"]
    assert run_input["maxItems"] == 25
    assert "searchTerms" not in run_input    # only one input mode populated
    assert artifact["items"][0]["text"] == "hi"


async def test_apify_twitter_search_mode_runs_actor():
    client, actor, dataset = _fake_client([{"text": "hello"}])
    tool = ApifyTwitterTool(client=client, actor_id="apidojo~twitter-scraper-lite")
    await tool._arun(search_query="jane doe", max_items=10)
    run_input = actor.call.call_args.kwargs["run_input"]
    assert run_input["searchTerms"] == ["jane doe"]
    assert run_input["maxItems"] == 10
    assert "twitterHandles" not in run_input


async def test_apify_twitter_requires_handle_or_query():
    tool = ApifyTwitterTool(client=MagicMock(), actor_id="x")
    with pytest.raises(ValueError):
        await tool._arun()


async def test_apify_metadata():
    ig = ApifyInstagramTool(client=MagicMock(), actor_id="x")
    li = ApifyLinkedInTool(client=MagicMock(), actor_id="x")
    tw = ApifyTwitterTool(client=MagicMock(), actor_id="x")
    assert ig.name == "apify_instagram"
    assert li.name == "apify_linkedin"
    assert tw.name == "apify_twitter"
    assert ig.response_format == "content_and_artifact"
    assert li.response_format == "content_and_artifact"
    assert tw.response_format == "content_and_artifact"
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_tools_apify.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement `osint/tools/apify.py`**

```python
import json
import os
from typing import Any, Type

from apify_client import ApifyClientAsync
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr

from osint.errors import ScanConfigError


DEFAULT_IG_ACTOR = "apify~instagram-scraper"
DEFAULT_LI_ACTOR = "apify~linkedin-profile-scraper"
DEFAULT_TW_ACTOR = "apidojo~twitter-scraper-lite"   # popular maintained X/Twitter scraper


def _get_client() -> ApifyClientAsync:
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise ScanConfigError("APIFY_TOKEN is not set")
    return ApifyClientAsync(token=token)


async def _run_actor(client: ApifyClientAsync, actor_id: str, run_input: dict) -> dict:
    run = await client.actor(actor_id).call(run_input=run_input)
    dataset_id = run["defaultDatasetId"]
    listing = await client.dataset(dataset_id).list_items()
    items = getattr(listing, "items", None) or []
    return {"items": items, "raw": {"default_dataset_id": dataset_id, "items": items}}


class _IGInput(BaseModel):
    username: str = Field(description="Instagram handle, without @.")
    results_limit: int = Field(default=20, ge=1, le=50)


class ApifyInstagramTool(BaseTool):
    name: str = "apify_instagram"
    description: str = (
        "Fetch an Instagram user's public profile and recent posts. Use when "
        "you have a confirmed Instagram handle."
    )
    args_schema: Type[BaseModel] = _IGInput
    response_format: str = "content_and_artifact"

    _client: ApifyClientAsync | None = PrivateAttr(default=None)
    _actor_id: str = PrivateAttr()

    def __init__(self, client: ApifyClientAsync | None = None, actor_id: str = DEFAULT_IG_ACTOR):
        super().__init__()
        self._client = client
        self._actor_id = actor_id

    @property
    def client(self) -> ApifyClientAsync:
        if self._client is None:
            self._client = _get_client()
        return self._client

    def _run(self, *args, **kwargs):
        raise NotImplementedError("Use async invocation.")

    async def _arun(self, username: str, results_limit: int = 20, **_: Any) -> tuple[str, dict]:
        artifact = await _run_actor(self.client, self._actor_id, {
            "usernames": [username],
            "resultsLimit": results_limit,
        })
        content = json.dumps({"items": artifact["items"]}, default=str)[:4000]
        return content, artifact


class _LIInput(BaseModel):
    profile_url: str = Field(description="Full https://www.linkedin.com/in/... URL")


class ApifyLinkedInTool(BaseTool):
    name: str = "apify_linkedin"
    description: str = (
        "Fetch a LinkedIn profile via an Apify scraper. Requires the public "
        "profile URL. Returns positions, education, skills, and connections count."
    )
    args_schema: Type[BaseModel] = _LIInput
    response_format: str = "content_and_artifact"

    _client: ApifyClientAsync | None = PrivateAttr(default=None)
    _actor_id: str = PrivateAttr()

    def __init__(self, client: ApifyClientAsync | None = None, actor_id: str = DEFAULT_LI_ACTOR):
        super().__init__()
        self._client = client
        self._actor_id = actor_id

    @property
    def client(self) -> ApifyClientAsync:
        if self._client is None:
            self._client = _get_client()
        return self._client

    def _run(self, *args, **kwargs):
        raise NotImplementedError("Use async invocation.")

    async def _arun(self, profile_url: str, **_: Any) -> tuple[str, dict]:
        artifact = await _run_actor(self.client, self._actor_id, {"profileUrls": [profile_url]})
        content = json.dumps({"items": artifact["items"]}, default=str)[:4000]
        return content, artifact


class _TwitterInput(BaseModel):
    handle: str | None = Field(
        default=None,
        description="X handle to fetch profile + recent tweets for. Without @. "
                    "Mutually exclusive with `search_query`.",
    )
    search_query: str | None = Field(
        default=None,
        description="Search X for tweets matching this query (e.g. a person's "
                    "full name in quotes). Mutually exclusive with `handle`.",
    )
    max_items: int = Field(default=20, ge=1, le=100)


class ApifyTwitterTool(BaseTool):
    name: str = "apify_twitter"
    description: str = (
        "Fetch X (Twitter) content via an Apify scraper. Two modes: pass a "
        "`handle` to fetch a specific user's profile + recent tweets, OR pass "
        "a `search_query` to search tweets across all of X. Use this for ANY "
        "X-native content; X's public surface is poorly indexed by general "
        "web search."
    )
    args_schema: Type[BaseModel] = _TwitterInput
    response_format: str = "content_and_artifact"

    _client: ApifyClientAsync | None = PrivateAttr(default=None)
    _actor_id: str = PrivateAttr()

    def __init__(self, client: ApifyClientAsync | None = None, actor_id: str = DEFAULT_TW_ACTOR):
        super().__init__()
        self._client = client
        self._actor_id = actor_id

    @property
    def client(self) -> ApifyClientAsync:
        if self._client is None:
            self._client = _get_client()
        return self._client

    def _run(self, *args, **kwargs):
        raise NotImplementedError("Use async invocation.")

    async def _arun(
        self,
        handle: str | None = None,
        search_query: str | None = None,
        max_items: int = 20,
        **_: Any,
    ) -> tuple[str, dict]:
        if not handle and not search_query:
            raise ValueError("apify_twitter requires either `handle` or `search_query`.")
        run_input: dict[str, Any] = {"maxItems": max_items}
        if handle:
            run_input["twitterHandles"] = [handle]
        else:
            run_input["searchTerms"] = [search_query]
        artifact = await _run_actor(self.client, self._actor_id, run_input)
        content = json.dumps({"items": artifact["items"]}, default=str)[:4000]
        return content, artifact
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_tools_apify.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/tools/apify.py tests/test_tools_apify.py
git commit -m "feat(tools): add Apify Instagram, LinkedIn, and Twitter as BaseTools"
```

---

## Task 10: Tavily tools — re-export `langchain-tavily`

**Files:**
- Create: `osint/tools/tavily.py`
- Create: `tests/test_tools_tavily.py`

Tavily search and extract already exist as LangChain tools in the `langchain-tavily` package. We just re-export them with the names the rest of the system uses, verify their names match our config, and check that `TAVILY_API_KEY` is required when the caller relies on the env-default construction.

- [ ] **Step 1: Write failing tests**

Create `tests/test_tools_tavily.py`:

```python
import os

import pytest

from osint.tools.tavily import make_tavily_search, make_tavily_extract


def test_tavily_tools_names(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    search = make_tavily_search()
    extract = make_tavily_extract()
    assert search.name == "tavily_search"
    assert extract.name == "tavily_extract"


def test_tavily_search_requires_api_key(monkeypatch):
    from osint.errors import ScanConfigError
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(ScanConfigError):
        make_tavily_search()
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_tools_tavily.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement `osint/tools/tavily.py`**

```python
import os

from langchain_tavily import TavilyExtract, TavilySearch

from osint.errors import ScanConfigError


def _require_key() -> str:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise ScanConfigError("TAVILY_API_KEY is not set")
    return key


def make_tavily_search(max_results: int = 20) -> TavilySearch:
    """Return langchain-tavily's TavilySearch tool, named `tavily_search`.

    `langchain-tavily` already names this tool `tavily_search`, so no rename.
    """
    _require_key()
    # Rely on TAVILY_API_KEY env var for the underlying client.
    return TavilySearch(max_results=max_results)


def make_tavily_extract() -> TavilyExtract:
    """Return langchain-tavily's TavilyExtract tool, named `tavily_extract`."""
    _require_key()
    return TavilyExtract()
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_tools_tavily.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/tools/tavily.py tests/test_tools_tavily.py
git commit -m "feat(tools): use langchain-tavily's built-in TavilySearch and TavilyExtract"
```

---

## Task 11: Prompts and report parser

**Files:**
- Create: `osint/prompts.py`
- Create: `tests/test_prompts.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_prompts.py`:

```python
from osint.prompts import build_system_prompt, build_synthesis_prompt, parse_report


def test_system_prompt_contains_subject_and_tools():
    p = build_system_prompt(
        subject="Jane, NYC, @jdoe",
        tool_names=["tavily_search", "maigret"],
    )
    assert "Jane, NYC, @jdoe" in p
    assert "tavily_search" in p
    assert "maigret" in p
    assert "extracted_identifiers" in p
    assert "```json" in p


def test_system_prompt_routes_x_content_to_apify_twitter_when_enabled():
    p = build_system_prompt(
        subject="Jane",
        tool_names=["tavily_search", "apify_twitter"],
    )
    # The prompt must explicitly tell the agent to use apify_twitter for X
    # content rather than relying on tool-description inference.
    assert "apify_twitter" in p
    assert "X (Twitter)" in p or "X content" in p or "X-native" in p


def test_system_prompt_omits_x_routing_when_apify_twitter_disabled():
    p = build_system_prompt(subject="Jane", tool_names=["tavily_search", "maigret"])
    # When apify_twitter isn't enabled, don't tell the LLM to use it.
    assert "apify_twitter" not in p


def test_synthesis_prompt_mentions_stop_reason():
    p = build_synthesis_prompt(stop_reason="budget")
    assert "budget" in p.lower()


def test_parse_report_from_fenced_json():
    text = 'stuff\n```json\n{"extracted_identifiers": {"emails": ["j@e"]}, "report": {"summary": "hi"}}\n```\nmore'
    r = parse_report(text)
    assert r["extracted_identifiers"] == {"emails": ["j@e"]}
    assert r["report"] == {"summary": "hi"}


def test_parse_report_falls_back_on_invalid_json():
    r = parse_report("no json here")
    assert r["extracted_identifiers"] == {}
    assert r["report"] == {"text": "no json here"}


def test_parse_report_handles_bare_json():
    r = parse_report('{"extracted_identifiers": {}, "report": {"x": 1}}')
    assert r["report"] == {"x": 1}
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_prompts.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement `osint/prompts.py`**

```python
import json
import re


SYSTEM_TEMPLATE = """\
You are a self-OSINT agent. The user wants to know what is publicly
discoverable about themselves online. The subject is the caller; the caller
has consented.

SUBJECT DESCRIPTION:
---
{subject}
---

Steps:
1. Parse the description into a structured set of identifiers (emails, phones,
   usernames, full-name variants, schools, employers, cities, platform URLs).
2. Use the tools below to investigate. Call multiple tools in the same turn
   when queries are independent; prefer cheap/broad tools before paid/narrow.
3. Extract as much as possible from each tool response before spending more.
4. Stop calling tools when nothing new is likely to surface or when you
   believe you have enough evidence.

Available tools: {tool_names}

Routing guidance (use the right tool for the job, not whichever happens to
match the description first):
{routing_guidance}

When you are ready to finish, return ONLY a single assistant message with NO
tool calls, containing one fenced JSON block of this exact shape:

```json
{{
  "extracted_identifiers": {{ "emails": [...], "usernames": [...], "urls": [...] }},
  "report": {{
    "summary": "...",
    "accounts": [...],
    "web_presence": [...],
    "exposures": [...],
    "remediation": [...]
  }}
}}
```

The schema above is a guideline — add fields as needed. The fenced JSON is
what the user will read, so populate it fully.
"""


# Per-tool one-line routing rules, only included for tools actually enabled.
_ROUTING_RULES = {
    "tavily_search": "tavily_search — general web (news, blogs, personal sites, public profiles outside of X). The default for any open-web question.",
    "tavily_extract": "tavily_extract — read the full content of a specific URL you already have. Use after a search hit looks promising.",
    "maigret": "maigret — given a confirmed/likely username, map which sites that handle exists on. Don't use for general search; only when you have an actual username.",
    "apify_instagram": "apify_instagram — fetch a specific Instagram profile and recent posts. Requires a confirmed handle.",
    "apify_linkedin": "apify_linkedin — fetch a specific LinkedIn profile by full URL.",
    "apify_twitter": "apify_twitter — for ANY X (Twitter) content: pass `handle` to fetch a specific user's profile + recent tweets, or pass `search_query` to search tweets across X (e.g. for posts about the subject). Don't use tavily_search for X content; X's public surface is poorly indexed by general web search.",
}


SYNTHESIS_TEMPLATE = """\
The scan was cut short. Reason: {stop_reason}.

Based on the tool calls already made and their results in the conversation so
far, produce the final report now. Return ONLY a fenced JSON block with the
shape:

```json
{{
  "extracted_identifiers": {{...}},
  "report": {{...}}
}}
```
"""


def build_system_prompt(subject: str, tool_names: list[str]) -> str:
    rules = [f"- {_ROUTING_RULES[n]}" for n in tool_names if n in _ROUTING_RULES]
    routing_guidance = "\n".join(rules) if rules else "- (no enabled tools have specific routing rules)"
    return SYSTEM_TEMPLATE.format(
        subject=subject,
        tool_names=", ".join(tool_names),
        routing_guidance=routing_guidance,
    )


def build_synthesis_prompt(stop_reason: str) -> str:
    return SYNTHESIS_TEMPLATE.format(stop_reason=stop_reason)


_FENCED_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def parse_report(text: str) -> dict:
    text = text or ""
    candidates = []
    m = _FENCED_JSON.search(text)
    if m:
        candidates.append(m.group(1))
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    for c in candidates:
        try:
            data = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return {
                "extracted_identifiers": data.get("extracted_identifiers") or {},
                "report": data.get("report") or {},
            }
    return {"extracted_identifiers": {}, "report": {"text": text}}
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_prompts.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/prompts.py tests/test_prompts.py
git commit -m "feat(prompts): add system + synthesis prompts and report parser"
```

---

## Task 12: Tool factory — assemble enabled tools

**Files:**
- Modify: `osint/tools/__init__.py`
- Create: `tests/test_tools_factory.py`

A single factory takes `(config, state)` and returns the list of `CappedTool`-wrapped tools the agent should see. This centralizes the cost-per-tool lookup and the env-var presence check — missing keys for enabled tools raise `ScanConfigError` *before* `scan()` boots the agent.

- [ ] **Step 1: Write failing tests**

Create `tests/test_tools_factory.py`:

```python
import pytest

from osint.errors import ScanConfigError
from osint.state import ScanState
from osint.tools import build_tools
from osint.types import ScanConfig


def test_build_tools_free_tier_without_keys_raises(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    with pytest.raises(ScanConfigError):
        build_tools(ScanConfig(), state)


def test_build_tools_with_keys(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    tools = build_tools(ScanConfig(enabled_tools={"tavily_search", "maigret"}), state)
    names = sorted(t.name for t in tools)
    assert names == ["maigret", "tavily_search"]


def test_build_tools_paid_requires_their_keys(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    cfg = ScanConfig(enabled_tools={"tavily_search", "apify_instagram"})
    with pytest.raises(ScanConfigError):
        build_tools(cfg, state)


def test_build_tools_rejects_unknown_name(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    state = ScanState(scan_id="x", subject="s", config=ScanConfig())
    with pytest.raises(ScanConfigError):
        build_tools(ScanConfig(enabled_tools={"nope"}), state)
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_tools_factory.py -v`
Expected: `ImportError` or related.

- [ ] **Step 3: Implement the factory**

Overwrite `osint/tools/__init__.py`:

```python
import os

from langchain_core.tools import BaseTool

from osint.capped_tool import CappedTool
from osint.errors import ScanConfigError
from osint.state import ScanState
from osint.tools.apify import (
    ApifyInstagramTool,
    ApifyLinkedInTool,
    ApifyTwitterTool,
)
from osint.tools.maigret import MaigretTool
from osint.tools.tavily import make_tavily_extract, make_tavily_search
from osint.types import ScanConfig


_COSTS = {
    "tavily_search": 0.004,
    "tavily_extract": 0.001,
    "maigret": 0.0,
    "apify_instagram": 0.15,
    "apify_linkedin": 0.05,
    "apify_twitter": 0.05,
}


def _require_env(var: str, tool: str) -> None:
    if not os.environ.get(var):
        raise ScanConfigError(f"{tool} enabled but {var} is not set")


def _make_raw_tool(name: str, config: ScanConfig) -> BaseTool:
    if name == "tavily_search":
        _require_env("TAVILY_API_KEY", name)
        return make_tavily_search()
    if name == "tavily_extract":
        _require_env("TAVILY_API_KEY", name)
        return make_tavily_extract()
    if name == "maigret":
        opts = config.tool_options.get("maigret", {})
        return MaigretTool(proxy_url=opts.get("proxy_url"))
    if name == "apify_instagram":
        _require_env("APIFY_TOKEN", name)
        return ApifyInstagramTool()
    if name == "apify_linkedin":
        _require_env("APIFY_TOKEN", name)
        return ApifyLinkedInTool()
    if name == "apify_twitter":
        _require_env("APIFY_TOKEN", name)
        return ApifyTwitterTool()
    raise ScanConfigError(f"unknown tool: {name}")


def build_tools(config: ScanConfig, state: ScanState) -> list[CappedTool]:
    tools: list[CappedTool] = []
    for name in sorted(config.enabled_tools):
        raw = _make_raw_tool(name, config)
        tools.append(CappedTool(wrapped=raw, state=state, est_cost_usd=_COSTS.get(name, 0.0)))
    return tools
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_tools_factory.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/tools/__init__.py tests/test_tools_factory.py
git commit -m "feat(tools): add build_tools factory with API-key preflight"
```

---

## Task 13: Structlog setup

**Files:**
- Create: `osint/log.py`

- [ ] **Step 1: Implement logging setup**

Create `osint/log.py`:

```python
import logging
import sys

import structlog


def configure_logging(level: int = logging.INFO) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


logger = structlog.get_logger("osint")
```

- [ ] **Step 2: Smoke test via the REPL**

Run: `python -c "from osint.log import configure_logging, logger; configure_logging(); logger.info('hi', x=1)"`
Expected: one structured line on stderr including `event=hi x=1`.

- [ ] **Step 3: Commit**

```bash
git add osint/log.py
git commit -m "chore(log): add structlog setup"
```

---

## Task 13.5: `LLMCostCallback` — capture token usage from every LLM call

**Files:**
- Create: `osint/llm_cost.py`
- Create: `tests/test_llm_cost.py`

LangChain callbacks fire on every LLM call (inside the ReAct agent *and* inside our synthesis call). The callback parses the response's usage metadata — available at `LLMResult.llm_output["token_usage"]` (OpenAI-style) and/or on `AIMessage.usage_metadata` (LangChain-standardized) — and accumulates counts on `ScanState`. No v1 tool makes its own LLM call, so the callback covers all token spend.

- [ ] **Step 1: Write failing tests**

Create `tests/test_llm_cost.py`:

```python
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from osint.llm_cost import LLMCostCallback
from osint.state import ScanState
from osint.types import ScanConfig


def _llm_result_with_token_usage(prompt: int, completion: int) -> LLMResult:
    ai = AIMessage(content="ok")
    gen = ChatGeneration(message=ai)
    return LLMResult(
        generations=[[gen]],
        llm_output={"token_usage": {"prompt_tokens": prompt, "completion_tokens": completion}},
    )


def _llm_result_with_usage_metadata(inp: int, out: int) -> LLMResult:
    ai = AIMessage(content="ok",
                   usage_metadata={"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out})
    gen = ChatGeneration(message=ai)
    return LLMResult(generations=[[gen]])


async def test_callback_picks_up_openai_style_token_usage():
    state = ScanState(scan_id="x", subject="S", config=ScanConfig())
    cb = LLMCostCallback(state)
    await cb.on_llm_end(_llm_result_with_token_usage(prompt=1_000, completion=500))
    assert state.llm_input_tokens == 1_000
    assert state.llm_output_tokens == 500


async def test_callback_picks_up_usage_metadata_fallback():
    state = ScanState(scan_id="x", subject="S", config=ScanConfig())
    cb = LLMCostCallback(state)
    await cb.on_llm_end(_llm_result_with_usage_metadata(inp=2_000, out=250))
    assert state.llm_input_tokens == 2_000
    assert state.llm_output_tokens == 250


async def test_callback_is_silent_when_usage_is_missing():
    state = ScanState(scan_id="x", subject="S", config=ScanConfig())
    cb = LLMCostCallback(state)
    await cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok"))]]))
    assert state.llm_input_tokens == 0
    assert state.llm_output_tokens == 0
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_llm_cost.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement `osint/llm_cost.py`**

```python
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

from osint.state import ScanState


class LLMCostCallback(AsyncCallbackHandler):
    """Accumulate LLM token usage into a ScanState.

    Tries two places for token counts, in order:
    1. `response.llm_output["token_usage"]` — the OpenAI/xAI-compatible shape.
    2. Per-generation `message.usage_metadata` — the LangChain-standardized shape.
    """

    def __init__(self, state: ScanState):
        self.state = state

    async def on_llm_end(self, response: LLMResult, **_: Any) -> None:
        usage = (response.llm_output or {}).get("token_usage") or {}
        prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0

        if not prompt and not completion:
            # Fall back to per-generation usage_metadata on the AIMessage.
            for gens in response.generations:
                for gen in gens:
                    msg = getattr(gen, "message", None)
                    meta = getattr(msg, "usage_metadata", None) if msg is not None else None
                    if meta:
                        prompt += meta.get("input_tokens", 0) or 0
                        completion += meta.get("output_tokens", 0) or 0

        if prompt or completion:
            self.state.record_llm_usage(
                input_tokens=int(prompt or 0),
                output_tokens=int(completion or 0),
            )
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_llm_cost.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/llm_cost.py tests/test_llm_cost.py
git commit -m "feat(llm_cost): add LangChain callback that records token usage to ScanState"
```

---

## Task 14: `scan()` — the LangGraph agent loop

**Files:**
- Create: `osint/scan.py`
- Create: `tests/test_scan.py`

The function signature is unchanged. Internally:

1. Validate subject.
2. Build `ScanState` and `CappedTool`-wrapped tools via `build_tools`.
3. Build a Grok `ChatOpenAI` (or use the injected `llm`).
4. Construct a ReAct agent via `langgraph.prebuilt.create_react_agent(model, tools)`.
5. Invoke with `recursion_limit = 2 * max_tool_calls` (each iteration is an LLM call + potential tool round-trip) and an outer `asyncio.wait_for(..., timeout=max_wall_clock_sec)`.
6. On success: take the final assistant message's content, `parse_report`, record, write JSON, return.
7. On `ScanStopped` (from `CappedTool`): call the LLM once with the synthesis prompt on the current message history, parse, write, return.
8. On `asyncio.TimeoutError` or `GraphRecursionError`: same synthesis fallback.
9. On any other exception: write a `status="failed"` JSON and re-raise.

- [ ] **Step 1: Write failing tests**

Create `tests/test_scan.py`:

```python
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolCall

from osint.scan import scan
from osint.types import ScanConfig


def _ai_with_tool_call(name: str, args: dict, tool_id: str = "call_1", text: str = "thinking") -> AIMessage:
    return AIMessage(
        content=text,
        tool_calls=[ToolCall(name=name, args=args, id=tool_id)],
    )


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
    """LLM skips tools and emits a final JSON immediately."""
    fake = FakeMessagesListChatModel(responses=[_ai_final(FINAL_JSON)])
    result = await scan(
        subject="Jane, j@e",
        config=ScanConfig(enabled_tools={"tavily_search"}),
        llm=fake,
        scans_dir=tmp_path,
    )
    assert result.report == {"summary": "hi"}
    assert result.extracted_identifiers == {"emails": ["j@e"]}
    assert result.path.exists()


async def test_scan_writes_failed_json_on_unexpected_error(tmp_path, monkeypatch):
    from osint import scan as scan_module
    monkeypatch.setattr(scan_module, "create_react_agent",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        await scan(subject="Jane", config=ScanConfig(enabled_tools={"tavily_search"}),
                   llm=MagicMock(), scans_dir=tmp_path)
    # A failed JSON should still be written.
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    import json
    data = json.loads(files[0].read_text())
    assert data["status"] == "failed"


async def test_scan_synthesizes_on_scan_stopped(tmp_path, monkeypatch):
    from osint import scan as scan_module
    from osint.errors import ScanStopped

    # Simulate the agent raising ScanStopped mid-flight (a cap was hit inside
    # CappedTool during some tool call). The synthesis LLM call should be made.
    async def raise_stopped(*_a, **_k):
        raise ScanStopped("budget")

    fake_agent = MagicMock()
    fake_agent.ainvoke = AsyncMock(side_effect=raise_stopped)
    monkeypatch.setattr(scan_module, "create_react_agent", lambda *a, **k: fake_agent)

    synth_llm = MagicMock()
    synth_llm.ainvoke = AsyncMock(return_value=AIMessage(content=FINAL_JSON))

    result = await scan(
        subject="Jane",
        config=ScanConfig(enabled_tools={"tavily_search"}),
        llm=synth_llm,
        scans_dir=tmp_path,
    )
    assert result.report == {"summary": "hi"}
    # synth_llm.ainvoke is called twice in this setup: once as the agent's
    # injected model (which we intercept via create_react_agent) and once for
    # synthesis. Since we replaced create_react_agent, only synthesis counts.
    assert synth_llm.ainvoke.await_count == 1


async def test_scan_synthesizes_on_timeout(tmp_path, monkeypatch):
    from osint import scan as scan_module
    import asyncio

    async def hang(*_a, **_k):
        await asyncio.sleep(10)

    fake_agent = MagicMock()
    fake_agent.ainvoke = AsyncMock(side_effect=hang)
    monkeypatch.setattr(scan_module, "create_react_agent", lambda *a, **k: fake_agent)

    synth_llm = MagicMock()
    synth_llm.ainvoke = AsyncMock(return_value=AIMessage(content=FINAL_JSON))

    result = await scan(
        subject="Jane",
        config=ScanConfig(enabled_tools={"tavily_search"}, max_wall_clock_sec=1),
        llm=synth_llm,
        scans_dir=tmp_path,
    )
    assert result.report == {"summary": "hi"}
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_scan.py -v`
Expected: `ImportError` on `osint.scan`.

- [ ] **Step 3: Implement `osint/scan.py`**

```python
import asyncio
import os
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from osint.errors import ScanConfigError, ScanStopped
from osint.llm_cost import LLMCostCallback
from osint.log import configure_logging, logger
from osint.prompts import build_synthesis_prompt, build_system_prompt, parse_report
from osint.state import ScanState, StopReason
from osint.storage import new_scan_id, write_scan_json
from osint.tools import build_tools
from osint.types import ScanConfig, ScanResult


def _default_llm() -> ChatOpenAI:
    key = os.environ.get("XAI_API_KEY")
    if not key:
        raise ScanConfigError("XAI_API_KEY is not set")
    return ChatOpenAI(
        model="grok-4.20",
        base_url="https://api.x.ai/v1",
        api_key=key,
    )


async def _synthesize(
    llm: BaseChatModel,
    subject: str,
    state: ScanState,
    stop_reason: str,
    cost_cb: LLMCostCallback,
) -> str:
    msgs = [
        SystemMessage(content=build_system_prompt(subject, sorted(state.config.enabled_tools))),
        HumanMessage(content=build_synthesis_prompt(stop_reason)),
    ]
    result = await llm.ainvoke(msgs, config={"callbacks": [cost_cb]})
    return result.content or ""


def _extract_final_text(agent_result: dict) -> str:
    """Pull the last AI message's content string from a LangGraph agent result."""
    messages = agent_result.get("messages", [])
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            return m.content or ""
    return ""


async def scan(
    subject: str,
    config: ScanConfig = ScanConfig(),
    llm: BaseChatModel | None = None,
    scans_dir: Path = Path("./scans"),
) -> ScanResult:
    if not subject or not subject.strip():
        raise ValueError("subject must be a non-empty description")
    configure_logging()

    llm = llm or _default_llm()
    state = ScanState(scan_id=new_scan_id(), subject=subject, config=config)
    logger.info("scan.start", scan_id=state.scan_id, enabled_tools=sorted(config.enabled_tools))

    try:
        tools = build_tools(config, state)

        agent = create_react_agent(
            llm,
            tools,
            state_modifier=SystemMessage(
                content=build_system_prompt(subject, sorted(config.enabled_tools))
            ),
        )

        cost_cb = LLMCostCallback(state)
        initial_state = {"messages": [HumanMessage(content="Begin the scan.")]}
        invoke_config: dict[str, Any] = {
            "recursion_limit": 2 * config.max_tool_calls,
            "callbacks": [cost_cb],
        }

        stop_reason: StopReason | None = None
        agent_result: dict | None = None
        try:
            agent_result = await asyncio.wait_for(
                agent.ainvoke(initial_state, config=invoke_config),
                timeout=config.max_wall_clock_sec,
            )
        except ScanStopped as e:
            stop_reason = StopReason(e.reason)
        except asyncio.TimeoutError:
            stop_reason = StopReason.WALL_CLOCK
        except GraphRecursionError:
            stop_reason = StopReason.MAX_CALLS

        if stop_reason is None and agent_result is not None:
            final_text = _extract_final_text(agent_result)
            parsed = parse_report(final_text)
            state.record_final_report(parsed["report"], identifiers=parsed["extracted_identifiers"])
        else:
            logger.info("scan.synthesize", scan_id=state.scan_id,
                        stop_reason=stop_reason.value if stop_reason else "unknown")
            synth_text = await _synthesize(
                llm, subject, state, stop_reason.value if stop_reason else "unknown", cost_cb,
            )
            parsed = parse_report(synth_text)
            state.record_final_report(parsed["report"], identifiers=parsed["extracted_identifiers"])

        path = await write_scan_json(scans_dir, state, status="done")
        logger.info(
            "scan.done",
            scan_id=state.scan_id,
            tool_calls=len(state.tool_calls),
            tool_cost_usd=state.tool_cost_usd,
            llm_cost_usd=state.llm_cost_usd,
            total_cost_usd=state.total_cost_usd,
            llm_input_tokens=state.llm_input_tokens,
            llm_output_tokens=state.llm_output_tokens,
            duration_sec=state.wall_clock_elapsed,
        )
        return ScanResult(
            scan_id=state.scan_id,
            subject=subject,
            extracted_identifiers=state.extracted_identifiers,
            report=state.report,
            tool_calls=state.tool_calls,
            total_cost_usd=state.total_cost_usd,
            duration_sec=state.wall_clock_elapsed,
            path=path,
        )
    except Exception:
        # Best-effort: persist whatever state we have so the failure is
        # auditable. If THIS write also fails, log the secondary error and
        # let the original exception propagate (do not mask it with the
        # secondary one — the original is what the caller needs to see).
        try:
            await write_scan_json(scans_dir, state, status="failed")
        except Exception as secondary:
            logger.error(
                "scan.failed_write_failed",
                scan_id=state.scan_id,
                secondary_error=repr(secondary),
            )
        raise
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_scan.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/scan.py tests/test_scan.py
git commit -m "feat(scan): add LangGraph create_react_agent loop with synthesis fallbacks"
```

---

## Task 15: CLI and package exports

**Files:**
- Create: `osint/cli.py`
- Modify: `osint/__init__.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cli.py`:

```python
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from osint.cli import main


async def test_cli_passes_subject_to_scan(tmp_path: Path):
    fake = type("R", (), {})()
    fake.scan_id = "sid"
    fake.path = tmp_path / "sid.json"
    (tmp_path / "sid.json").write_text("{}")
    with patch("osint.cli.scan", new=AsyncMock(return_value=fake)) as m:
        await main(["scan", "Jane Doe, jane@e, @jdoe", "--scans-dir", str(tmp_path)])
    kwargs = m.call_args.kwargs
    assert kwargs["subject"] == "Jane Doe, jane@e, @jdoe"
    assert kwargs["scans_dir"] == tmp_path


async def test_cli_reads_stdin_when_no_arg(tmp_path: Path, monkeypatch):
    fake = type("R", (), {})()
    fake.scan_id = "sid"
    fake.path = tmp_path / "sid.json"
    (tmp_path / "sid.json").write_text("{}")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("Jane from stdin"))
    with patch("osint.cli.scan", new=AsyncMock(return_value=fake)) as m:
        await main(["scan", "--scans-dir", str(tmp_path)])
    assert m.call_args.kwargs["subject"] == "Jane from stdin"


async def test_cli_exits_nonzero_on_empty_subject(tmp_path: Path):
    with pytest.raises(SystemExit) as exc:
        await main(["scan", "  ", "--scans-dir", str(tmp_path)])
    assert exc.value.code != 0
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_cli.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement `osint/cli.py`**

```python
import argparse
import asyncio
import sys
from pathlib import Path

from osint.scan import scan
from osint.types import ScanConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m osint.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="Run a scan from a subject description.")
    s.add_argument("subject", nargs="?", default=None,
                   help="Free-form subject description. If omitted, read from stdin.")
    s.add_argument("--scans-dir", type=Path, default=Path("./scans"))
    s.add_argument("--budget-usd", type=float, default=5.0)
    s.add_argument("--max-calls", type=int, default=30)
    s.add_argument("--max-seconds", type=int, default=600)
    s.add_argument("--enable", action="append", default=None,
                   help="Enable a tool by name. Repeatable. Defaults to the standard free set.")
    return parser


async def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    subject = args.subject if args.subject is not None else sys.stdin.read()
    if not subject or not subject.strip():
        print("error: subject must be a non-empty description", file=sys.stderr)
        sys.exit(2)

    kwargs: dict = {
        "budget_usd": args.budget_usd,
        "max_tool_calls": args.max_calls,
        "max_wall_clock_sec": args.max_seconds,
    }
    if args.enable:
        kwargs["enabled_tools"] = set(args.enable)

    result = await scan(
        subject=subject,
        config=ScanConfig(**kwargs),
        scans_dir=args.scans_dir,
    )
    print(result.path)
    return 0


def _entry() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    _entry()
```

- [ ] **Step 4: Finalize `osint/__init__.py` exports**

Overwrite `osint/__init__.py`:

```python
from osint.errors import ScanConfigError, ScanStopped
from osint.scan import scan
from osint.types import ScanConfig, ScanResult, ToolCallRecord

__all__ = [
    "scan",
    "ScanConfig",
    "ScanResult",
    "ToolCallRecord",
    "ScanConfigError",
    "ScanStopped",
]
```

- [ ] **Step 5: Run all tests — expect pass**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add osint/cli.py osint/__init__.py tests/test_cli.py
git commit -m "feat(cli): add argparse CLI and finalize public exports"
```

---

## Self-review

**Spec coverage (§§1–11):**

- §4 in-scope items: ✓ async `scan()` (Task 14), single LLM vendor (Task 14 default LLM), six tools (Tasks 7–10), JSON-per-scan (Task 5), per-tool concurrency cap on Maigret (Task 7), budget/call/wall-clock caps (Tasks 4, 6, 14).
- §5 architecture: ✓ replaced the custom agent with LangGraph's ReAct agent; tool registry becomes the factory in Task 12.
- §6.1 free-form string input + empty-rejection: ✓ Tasks 14, 15.
- §6.2 agent loop + parallel dispatch + stop conditions: ✓ Task 14 (LangGraph handles parallel dispatch natively; caps enforced via `CappedTool` raising `ScanStopped` + `asyncio.wait_for` + `recursion_limit`).
- §6.3 tool contract: ✓ we replaced the custom `Tool` Protocol with LangChain's `BaseTool`; the contract is equivalent (name, description, input schema, async run).
- §6.4 LLM abstraction: ✓ accepts any `BaseChatModel`; default is `ChatOpenAI` pointed at xAI.
- §6.5 X-content tool: ✓ replaced grok_x_search with `apify_twitter` (Task 8). Apify-based scraper handles both per-handle profile fetches and X-wide search queries; no Responses API special case, no `_llm_usage` plumbing in CappedTool.
- §6.6 Maigret mitigations (internal max_connections=15, process semaphore, proxy_url, sites_filter): ✓ Task 7.
- §6.7 JSON output shape with `extracted_identifiers` and raw tool calls: ✓ Tasks 5, 14.
- §6.8 cap enforcement + final synthesis + LLM-plus-tool cost accounting (§6.8.1): ✓ Tasks 2 (LLMPricing), 4 (combined `total_cost_usd`), 5 (JSON cost breakdown), 13.5 (`LLMCostCallback`), 14 (callback wired into both agent invocation and synthesis call). All tools route through vendor APIs that the main agent's LangChain-instrumented LLM doesn't touch, so vendor-tool token usage is not double-counted.
- §6.9 structlog logging with scan_id, no subject PII: ✓ Task 13 + Task 14 (explicit fields on every log call are scan_id/duration/cost/call count).
- §7 v1 tool list: ✓ all six.
- §8 public API including `python -m osint.cli`: ✓ Task 15.
- §9 env-var requirements + `ScanConfigError`: ✓ Task 12 factory raises pre-flight; Tasks 9, 10 also preflight inside their constructors.

**Placeholder scan:** none. All TDD code blocks are concrete. One caveat called out in Task 6 about `tool_call_id` provenance in `CappedTool` — flagged as a known small area and given an explicit fallback.

**Type consistency:**
- `ToolCallRecord` is named consistently everywhere (was `ToolCall` in the older non-LangGraph plan; renamed to avoid clashing with LangChain's `ToolCall` type).
- `StopReason` values used in `ScanState.should_stop()` match the values used in `ScanStopped.reason` and `asyncio.TimeoutError`/`GraphRecursionError` mapping in `scan()`.
- `build_tools(config, state)` signature used identically in Task 12 and Task 14.
- `BaseTool`'s `response_format="content_and_artifact"` contract is used uniformly across Maigret, Apify×2, and Grok-X tools; `CappedTool` handles both that shape and the plain-string shape (Tavily tools, which return strings).

**Scope check:** one library, one plan. 15 tasks, ~1,600 lines of code + ~900 lines of tests.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-24-self-osint-v1-agent.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
