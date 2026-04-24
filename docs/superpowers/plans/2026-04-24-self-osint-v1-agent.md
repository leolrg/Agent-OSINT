# Self-OSINT v1 Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python library exposing `async def scan(subject: str, config) -> ScanResult` that drives a Grok-4.20 agent over six OSINT tools (tavily_search, tavily_extract, maigret, apify_instagram, apify_linkedin, grok_x_search) and writes a JSON-per-scan record with verbatim tool output.

**Architecture:** A single Python package (`osint/`) with a registry of async tools and an agent loop that interleaves Grok tool-use rounds with parallel tool dispatch via `asyncio.gather`. Each scan produces one JSON file in a configurable directory capturing the subject, every tool call's raw response, and the final LLM report. No HTTP, no database — library-shaped.

**Tech Stack:** Python 3.11+, Pydantic v2 for types, the `openai` async SDK against xAI's OpenAI-compatible endpoint, `tavily-python`, `apify-client`, `maigret` (library), `structlog` for logging. Tests use `pytest`, `pytest-asyncio`, `respx` for HTTP mocking, `pytest-mock` for general mocking.

**Spec reference:** `docs/superpowers/specs/2026-04-24-self-osint-backend-design.md`

---

## File Structure

```
osint/
├── __init__.py          # public exports: scan, ScanConfig, ScanResult
├── types.py             # Pydantic models: ScanConfig, ToolUse, ToolCall, LLMResponse, ScanResult
├── state.py             # ScanState (mutable scan bookkeeping)
├── storage.py           # write_scan_json, new_scan_id
├── errors.py            # ScanConfigError, ToolError
├── llm.py               # LLM Protocol + GrokLLM
├── prompts.py           # build_system_prompt, build_synthesis_prompt, parse_report
├── scan.py              # scan() entrypoint + agent loop
├── cli.py               # python -m osint.cli
├── log.py               # structlog setup
└── tools/
    ├── __init__.py      # Tool Protocol, REGISTRY, TOOL_LIMITS, invoke_tool
    ├── tavily.py        # TavilySearchTool, TavilyExtractTool
    ├── maigret.py       # MaigretTool
    ├── apify.py         # ApifyInstagramTool, ApifyLinkedInTool
    └── grok_x.py        # GrokXSearchTool

tests/
├── __init__.py
├── conftest.py
├── test_types.py
├── test_state.py
├── test_storage.py
├── test_llm.py
├── test_tools_registry.py
├── test_tools_invoke.py
├── test_tools_tavily.py
├── test_tools_maigret.py
├── test_tools_apify.py
├── test_tools_grok_x.py
├── test_prompts.py
├── test_scan.py
└── test_cli.py
```

Each file has one responsibility. Tools are one-file-per-vendor. All tests in `tests/` keyed by module under test.

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
description = "Self-OSINT backend — v1 agent"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.5",
    "openai>=1.40",
    "tavily-python>=0.5",
    "apify-client>=1.7",
    "maigret>=0.4.4",
    "structlog>=24.1",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-mock>=3.12",
    "respx>=0.21",
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
git commit -m "chore: scaffold osint package"
```

---

## Task 2: Config types (`ScanConfig`)

**Files:**
- Create: `osint/types.py`
- Create: `tests/test_types.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_types.py`:

```python
from osint.types import ScanConfig


def test_scanconfig_defaults():
    c = ScanConfig()
    assert c.enabled_tools == {"tavily_search", "tavily_extract", "maigret"}
    assert c.budget_usd == 5.0
    assert c.max_tool_calls == 30
    assert c.max_wall_clock_sec == 600
    assert c.tool_concurrency == {"maigret": 2}
    assert c.tool_options == {}


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
    )
    assert c.enabled_tools == {"tavily_search"}
    assert c.tool_options["maigret"]["proxy_url"] == "http://p:8080"
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/test_types.py -v`
Expected: `ImportError`/`ModuleNotFoundError` for `osint.types`.

- [ ] **Step 3: Implement `ScanConfig`**

Create `osint/types.py`:

```python
from pydantic import BaseModel, Field, PositiveFloat, PositiveInt


def default_enabled_tools() -> set[str]:
    return {"tavily_search", "tavily_extract", "maigret"}


def default_tool_concurrency() -> dict[str, int]:
    return {"maigret": 2}


class ScanConfig(BaseModel):
    enabled_tools: set[str] = Field(default_factory=default_enabled_tools)
    budget_usd: PositiveFloat = 5.0
    max_tool_calls: PositiveInt = 30
    max_wall_clock_sec: PositiveInt = 600
    tool_concurrency: dict[str, int] = Field(default_factory=default_tool_concurrency)
    tool_options: dict[str, dict] = Field(default_factory=dict)
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

## Task 3: Core runtime types (`ToolUse`, `ToolCall`, `LLMResponse`, `ScanResult`)

**Files:**
- Modify: `osint/types.py`
- Modify: `tests/test_types.py`

- [ ] **Step 1: Extend tests**

Append to `tests/test_types.py`:

```python
from datetime import datetime
from pathlib import Path
from osint.types import ToolUse, ToolCall, LLMResponse, ScanResult


def test_tooluse_roundtrip():
    t = ToolUse(id="call_1", name="tavily_search", input={"query": "x"})
    assert t.model_dump() == {"id": "call_1", "name": "tavily_search", "input": {"query": "x"}}


def test_toolcall_error_optional():
    tc = ToolCall(
        turn=1, tool="tavily_search", input={"q": "x"}, output={"r": 1}, raw={"r": 1},
        started_at=datetime(2026, 4, 24), completed_at=datetime(2026, 4, 24),
        cost_usd=0.01,
    )
    assert tc.error is None


def test_llmresponse_fields():
    r = LLMResponse(
        text="hello",
        tool_uses=[ToolUse(id="a", name="t", input={})],
        assistant_message_raw={"role": "assistant", "content": "hello"},
    )
    assert len(r.tool_uses) == 1


def test_scanresult_fields():
    s = ScanResult(
        scan_id="s1",
        subject="Jane Doe",
        extracted_identifiers={"emails": ["j@e"]},
        report={"summary": "..."},
        tool_calls=[],
        total_cost_usd=0.0,
        duration_sec=1.0,
        path=Path("/tmp/s1.json"),
    )
    assert s.scan_id == "s1"
    assert s.subject == "Jane Doe"
```

- [ ] **Step 2: Run — expect 4 new failures**

Run: `pytest tests/test_types.py -v`
Expected: 3 pass, 4 fail with `ImportError` on the new symbols.

- [ ] **Step 3: Implement new types**

Append to `osint/types.py`:

```python
from datetime import datetime
from pathlib import Path
from typing import Any


class ToolUse(BaseModel):
    id: str
    name: str
    input: dict[str, Any]


class ToolCall(BaseModel):
    turn: int
    tool: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    raw: Any
    started_at: datetime
    completed_at: datetime
    cost_usd: float
    error: str | None = None


class LLMResponse(BaseModel):
    text: str
    tool_uses: list[ToolUse] = Field(default_factory=list)
    assistant_message_raw: dict[str, Any]


class ScanResult(BaseModel):
    scan_id: str
    subject: str
    extracted_identifiers: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    duration_sec: float = 0.0
    path: Path
```

- [ ] **Step 4: Run — expect all pass**

Run: `pytest tests/test_types.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/types.py tests/test_types.py
git commit -m "feat(types): add runtime types for tool calls, LLM responses, scan results"
```

---

## Task 4: `ScanState` and stop conditions

**Files:**
- Create: `osint/state.py`
- Create: `osint/errors.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_state.py`:

```python
import time
from datetime import datetime, timezone
from osint.state import ScanState, StopReason
from osint.types import ScanConfig, ToolCall


def _tc(cost: float = 0.0) -> ToolCall:
    now = datetime.now(timezone.utc)
    return ToolCall(
        turn=1, tool="t", input={}, output={}, raw={},
        started_at=now, completed_at=now, cost_usd=cost,
    )


def test_fresh_state_does_not_stop():
    s = ScanState(scan_id="x", subject="S", config=ScanConfig())
    stop, _ = s.should_stop()
    assert stop is False


def test_stops_on_budget():
    cfg = ScanConfig(budget_usd=0.05)
    s = ScanState(scan_id="x", subject="S", config=cfg)
    s.record_tool_call(_tc(cost=0.04))
    s.record_tool_call(_tc(cost=0.02))
    stop, reason = s.should_stop()
    assert stop is True
    assert reason == StopReason.BUDGET


def test_stops_on_max_tool_calls():
    cfg = ScanConfig(max_tool_calls=2)
    s = ScanState(scan_id="x", subject="S", config=cfg)
    s.record_tool_call(_tc())
    s.record_tool_call(_tc())
    stop, reason = s.should_stop()
    assert stop is True
    assert reason == StopReason.MAX_CALLS


def test_stops_on_wall_clock(monkeypatch):
    cfg = ScanConfig(max_wall_clock_sec=1)
    s = ScanState(scan_id="x", subject="S", config=cfg)
    s.started_at = time.monotonic() - 5
    stop, reason = s.should_stop()
    assert stop is True
    assert reason == StopReason.WALL_CLOCK


def test_final_report_tracking():
    s = ScanState(scan_id="x", subject="S", config=ScanConfig())
    assert s.has_final_report() is False
    s.record_final_report({"summary": "hi"}, identifiers={"emails": []})
    assert s.has_final_report() is True
    assert s.report == {"summary": "hi"}
    assert s.extracted_identifiers == {"emails": []}
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_state.py -v`
Expected: `ImportError` for `osint.state`.

- [ ] **Step 3: Implement `errors.py` first**

Create `osint/errors.py`:

```python
class ScanConfigError(Exception):
    """Invalid or incomplete scan configuration (e.g. missing API key)."""


class ToolError(Exception):
    """A tool raised an error during execution."""
```

- [ ] **Step 4: Implement `ScanState`**

Create `osint/state.py`:

```python
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from osint.types import ScanConfig, ToolCall


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
    tool_calls: list[ToolCall] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)
    extracted_identifiers: dict[str, Any] = field(default_factory=dict)
    _has_report: bool = False

    @property
    def total_cost_usd(self) -> float:
        return sum(tc.cost_usd for tc in self.tool_calls)

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

    def record_tool_call(self, tc: ToolCall) -> None:
        self.tool_calls.append(tc)

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
from osint.types import ScanConfig, ToolCall


def test_new_scan_id_is_uuidish():
    sid = new_scan_id()
    assert len(sid) >= 32
    assert sid != new_scan_id()


async def test_write_scan_json(tmp_path: Path):
    state = ScanState(scan_id="abc123", subject="Jane", config=ScanConfig())
    now = datetime.now(timezone.utc)
    state.record_tool_call(ToolCall(
        turn=1, tool="tavily_search", input={"q": "x"},
        output={"results": []}, raw={"results": []},
        started_at=now, completed_at=now, cost_usd=0.004,
    ))
    state.record_final_report({"summary": "done"}, identifiers={"emails": ["j@e"]})

    path = await write_scan_json(tmp_path, state, status="done")

    assert path == tmp_path / "abc123.json"
    data = json.loads(path.read_text())
    assert data["scan_id"] == "abc123"
    assert data["subject"] == "Jane"
    assert data["status"] == "done"
    assert data["extracted_identifiers"] == {"emails": ["j@e"]}
    assert data["report"] == {"summary": "done"}
    assert len(data["tool_calls"]) == 1
    assert data["tool_calls"][0]["tool"] == "tavily_search"
    assert data["total_cost_usd"] == 0.004
    assert "created_at" in data and "completed_at" in data
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

## Task 6: LLM abstraction — `GrokLLM`

**Files:**
- Create: `osint/llm.py`
- Create: `tests/test_llm.py`

`GrokLLM` uses the `openai` async client pointed at xAI's endpoint (`https://api.x.ai/v1`). xAI's chat completions API is OpenAI-compatible for tool use. The LLM receives serialized `Tool` specs and returns an `LLMResponse` with any `tool_uses` parsed out.

- [ ] **Step 1: Write failing tests**

Create `tests/test_llm.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from osint.llm import GrokLLM
from osint.types import ToolUse


class _FakeToolSpec:
    name = "tavily_search"
    description = "Search the web"
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }


@pytest.fixture
def fake_tool():
    return _FakeToolSpec()


async def test_grokllm_parses_text_and_tool_uses(monkeypatch, fake_tool):
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock()]
    fake_resp.choices[0].message = MagicMock(
        content="thinking...",
        tool_calls=[
            MagicMock(
                id="call_a",
                function=MagicMock(name="tavily_search", arguments='{"query":"x"}'),
            )
        ],
    )
    fake_resp.choices[0].message.tool_calls[0].function.name = "tavily_search"
    fake_resp.model_dump.return_value = {"id": "r1", "choices": []}

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    llm = GrokLLM(api_key="k", model="grok-4.20", client=fake_client)
    resp = await llm.call(
        messages=[{"role": "user", "content": "hi"}],
        tools=[fake_tool],
    )

    assert resp.text == "thinking..."
    assert resp.tool_uses == [ToolUse(id="call_a", name="tavily_search", input={"query": "x"})]
    fake_client.chat.completions.create.assert_awaited_once()
    kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "grok-4.20"
    assert kwargs["tools"][0]["function"]["name"] == "tavily_search"


async def test_grokllm_handles_no_tool_calls(fake_tool):
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock()]
    fake_resp.choices[0].message = MagicMock(content="final answer", tool_calls=None)
    fake_resp.model_dump.return_value = {}
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    llm = GrokLLM(api_key="k", client=fake_client)
    resp = await llm.call(messages=[{"role": "user", "content": "x"}], tools=[fake_tool])

    assert resp.text == "final answer"
    assert resp.tool_uses == []
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_llm.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement `osint/llm.py`**

Create `osint/llm.py`:

```python
import json
import os
from typing import Any, Protocol, runtime_checkable

from openai import AsyncOpenAI

from osint.types import LLMResponse, ToolUse


@runtime_checkable
class _ToolLike(Protocol):
    name: str
    description: str
    input_schema: dict


@runtime_checkable
class LLM(Protocol):
    async def call(self, messages: list[dict], tools: list[_ToolLike]) -> LLMResponse: ...
    async def synthesize(self, messages: list[dict]) -> str: ...


def _tool_to_openai_schema(tool: _ToolLike) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


class GrokLLM:
    """xAI Grok via the OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "grok-4.20",
        base_url: str = "https://api.x.ai/v1",
        client: AsyncOpenAI | None = None,
    ):
        self.model = model
        self._client = client or AsyncOpenAI(
            api_key=api_key or os.environ.get("XAI_API_KEY"),
            base_url=base_url,
        )

    async def call(self, messages: list[dict], tools: list[_ToolLike]) -> LLMResponse:
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=[_tool_to_openai_schema(t) for t in tools] if tools else None,
            tool_choice="auto" if tools else None,
        )
        msg = resp.choices[0].message
        tool_uses: list[ToolUse] = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_uses.append(ToolUse(id=tc.id, name=tc.function.name, input=args))
        return LLMResponse(
            text=msg.content or "",
            tool_uses=tool_uses,
            assistant_message_raw=resp.model_dump(),
        )

    async def synthesize(self, messages: list[dict]) -> str:
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
        )
        return resp.choices[0].message.content or ""

    @property
    def client(self) -> AsyncOpenAI:
        return self._client
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_llm.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/llm.py tests/test_llm.py
git commit -m "feat(llm): add GrokLLM over xAI's OpenAI-compatible endpoint"
```

---

## Task 7: Tool Protocol and registry

**Files:**
- Modify: `osint/tools/__init__.py`
- Create: `tests/test_tools_registry.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_tools_registry.py`:

```python
import pytest

from osint.tools import REGISTRY, Tool, register


class _DummyTool:
    name = "dummy"
    description = "does nothing"
    input_schema = {"type": "object", "properties": {}}
    tier = "free"
    est_cost_usd_per_call = 0.0
    vendor = "none"
    direct_scraping = False
    internal_concurrency = None

    async def run(self, **kwargs):
        return {"ok": True}


def test_register_and_lookup():
    t = _DummyTool()
    register(t)
    try:
        assert REGISTRY["dummy"] is t
    finally:
        REGISTRY.pop("dummy", None)


def test_register_rejects_duplicate():
    t = _DummyTool()
    register(t)
    try:
        with pytest.raises(ValueError):
            register(_DummyTool())
    finally:
        REGISTRY.pop("dummy", None)


def test_tool_protocol_runtime_checkable():
    assert isinstance(_DummyTool(), Tool)
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_tools_registry.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement the registry**

Overwrite `osint/tools/__init__.py`:

```python
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    input_schema: dict
    tier: str                       # "free" | "paid"
    est_cost_usd_per_call: float
    vendor: str                     # informational grouping; not used for semaphores in v1
    direct_scraping: bool           # True only for tools that hit target sites from our IP
    internal_concurrency: int | None  # tool's own fanout cap, or None

    async def run(self, **kwargs: Any) -> dict: ...


REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if tool.name in REGISTRY:
        raise ValueError(f"tool already registered: {tool.name}")
    REGISTRY[tool.name] = tool
    return tool
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_tools_registry.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/tools/__init__.py tests/test_tools_registry.py
git commit -m "feat(tools): add Tool Protocol and registry"
```

---

## Task 8: `invoke_tool` — budget, concurrency, error handling

**Files:**
- Modify: `osint/tools/__init__.py`
- Create: `tests/test_tools_invoke.py`

`invoke_tool` runs one tool call and returns a `ToolCall`. It:
- Checks if the state has already hit a stop condition; if so, returns an error ToolCall and does NOT call the tool.
- Acquires `TOOL_LIMITS[tool.name]` if present (direct-scraping tools only).
- Calls `tool.run(**inputs)` inside a `try/except`.
- Records timings, cost, output, and any error into a `ToolCall`.

- [ ] **Step 1: Write failing tests**

Create `tests/test_tools_invoke.py`:

```python
import asyncio
from unittest.mock import AsyncMock

import pytest

from osint.state import ScanState
from osint.tools import TOOL_LIMITS, invoke_tool
from osint.types import ScanConfig, ToolUse


class _Stub:
    def __init__(self, name="stub", cost=0.01, scraping=False, raises=None, out=None):
        self.name = name
        self.description = "stub"
        self.input_schema = {"type": "object"}
        self.tier = "free"
        self.est_cost_usd_per_call = cost
        self.vendor = "none"
        self.direct_scraping = scraping
        self.internal_concurrency = None
        self._raises = raises
        self._out = out or {"ok": True}
        self.run = AsyncMock(side_effect=self._run)

    async def _run(self, **kwargs):
        if self._raises:
            raise self._raises
        return self._out


async def test_invoke_tool_success():
    t = _Stub()
    state = ScanState(scan_id="s", subject="x", config=ScanConfig())
    tu = ToolUse(id="c1", name="stub", input={"q": "x"})
    tc = await invoke_tool(t, tu, state, turn=1)
    assert tc.tool == "stub"
    assert tc.output == {"ok": True}
    assert tc.cost_usd == 0.01
    assert tc.error is None


async def test_invoke_tool_records_error_on_exception():
    t = _Stub(raises=RuntimeError("boom"))
    state = ScanState(scan_id="s", subject="x", config=ScanConfig())
    tu = ToolUse(id="c1", name="stub", input={})
    tc = await invoke_tool(t, tu, state, turn=1)
    assert tc.output is None
    assert "boom" in tc.error
    assert tc.cost_usd == 0.01


async def test_invoke_tool_skips_when_over_budget():
    t = _Stub()
    state = ScanState(scan_id="s", subject="x", config=ScanConfig(budget_usd=0.005))
    # inflate cost so state is already over budget
    from datetime import datetime, timezone
    from osint.types import ToolCall
    now = datetime.now(timezone.utc)
    state.record_tool_call(ToolCall(
        turn=0, tool="prev", input={}, output={}, raw={},
        started_at=now, completed_at=now, cost_usd=0.01,
    ))
    tu = ToolUse(id="c1", name="stub", input={})
    tc = await invoke_tool(t, tu, state, turn=1)
    assert t.run.await_count == 0
    assert tc.error is not None
    assert "stop" in tc.error.lower() or "budget" in tc.error.lower()


async def test_invoke_tool_uses_tool_semaphore_for_direct_scraping(monkeypatch):
    t = _Stub(name="maigret_like", scraping=True)
    TOOL_LIMITS[t.name] = asyncio.Semaphore(1)
    try:
        state = ScanState(scan_id="s", subject="x", config=ScanConfig())
        async def slow(**_):
            await asyncio.sleep(0.05)
            return {"ok": True}
        t.run = AsyncMock(side_effect=slow)
        tu1 = ToolUse(id="c1", name="maigret_like", input={})
        tu2 = ToolUse(id="c2", name="maigret_like", input={})
        results = await asyncio.gather(
            invoke_tool(t, tu1, state, turn=1),
            invoke_tool(t, tu2, state, turn=1),
        )
        # both should succeed; semaphore serialization means total elapsed ≥ 2*0.05
        assert all(r.error is None for r in results)
    finally:
        TOOL_LIMITS.pop(t.name, None)
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_tools_invoke.py -v`
Expected: `ImportError` on `invoke_tool` / `TOOL_LIMITS`.

- [ ] **Step 3: Extend `osint/tools/__init__.py`**

Append to `osint/tools/__init__.py`:

```python
import asyncio
import contextlib
from datetime import datetime, timezone

from osint.state import ScanState
from osint.types import ToolCall, ToolUse


TOOL_LIMITS: dict[str, asyncio.Semaphore] = {}


@contextlib.asynccontextmanager
async def _maybe_semaphore(sem: asyncio.Semaphore | None):
    if sem is None:
        yield
        return
    async with sem:
        yield


async def invoke_tool(
    tool: Tool,
    tool_use: ToolUse,
    state: ScanState,
    turn: int,
) -> ToolCall:
    started = datetime.now(timezone.utc)
    stopped, reason = state.should_stop()
    if stopped:
        return ToolCall(
            turn=turn, tool=tool.name, input=tool_use.input,
            output=None, raw=None,
            started_at=started, completed_at=started,
            cost_usd=0.0,
            error=f"skipped: scan stopped ({reason.value})",
        )

    sem = TOOL_LIMITS.get(tool.name) if tool.direct_scraping else None
    error: str | None = None
    output: dict | None = None
    raw = None
    try:
        async with _maybe_semaphore(sem):
            result = await tool.run(**tool_use.input)
        # Tools return a plain dict; raw is by convention the same dict
        # unless the tool nests raw vendor output under "raw".
        if isinstance(result, dict):
            output = result
            raw = result.get("raw", result)
        else:
            output = {"value": result}
            raw = result
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    completed = datetime.now(timezone.utc)
    return ToolCall(
        turn=turn, tool=tool.name, input=tool_use.input,
        output=output, raw=raw,
        started_at=started, completed_at=completed,
        cost_usd=tool.est_cost_usd_per_call,
        error=error,
    )
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_tools_invoke.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/tools/__init__.py tests/test_tools_invoke.py
git commit -m "feat(tools): add invoke_tool with budget check and tool-level semaphore"
```

---

## Task 9: Tavily tools (`tavily_search` + `tavily_extract`)

**Files:**
- Create: `osint/tools/tavily.py`
- Create: `tests/test_tools_tavily.py`

Both tools share one async Tavily client. Each returns `{"result": ..., "raw": ...}` so the raw vendor response is captured.

- [ ] **Step 1: Write failing tests**

Create `tests/test_tools_tavily.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from osint.tools.tavily import TavilySearchTool, TavilyExtractTool


async def test_tavily_search_calls_client():
    client = MagicMock()
    client.search = AsyncMock(return_value={"results": [{"url": "https://x", "title": "t"}]})
    tool = TavilySearchTool(client=client)
    out = await tool.run(query="jane doe", max_results=5)
    client.search.assert_awaited_once_with(query="jane doe", max_results=5)
    assert out["results"][0]["url"] == "https://x"
    assert out["raw"]["results"][0]["url"] == "https://x"


async def test_tavily_extract_calls_client():
    client = MagicMock()
    client.extract = AsyncMock(return_value={"results": [{"url": "https://x", "raw_content": "hi"}]})
    tool = TavilyExtractTool(client=client)
    out = await tool.run(urls=["https://x"])
    client.extract.assert_awaited_once_with(urls=["https://x"])
    assert out["results"][0]["raw_content"] == "hi"


async def test_tavily_search_tool_metadata():
    tool = TavilySearchTool(client=MagicMock())
    assert tool.name == "tavily_search"
    assert tool.direct_scraping is False
    assert tool.tier == "free"
    assert "query" in tool.input_schema["properties"]


async def test_tavily_extract_tool_metadata():
    tool = TavilyExtractTool(client=MagicMock())
    assert tool.name == "tavily_extract"
    assert tool.direct_scraping is False
    assert "urls" in tool.input_schema["properties"]
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_tools_tavily.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement `osint/tools/tavily.py`**

```python
import os
from typing import Any

from tavily import AsyncTavilyClient

from osint.errors import ScanConfigError


def _get_client() -> AsyncTavilyClient:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise ScanConfigError("TAVILY_API_KEY is not set")
    return AsyncTavilyClient(api_key=key)


class TavilySearchTool:
    name = "tavily_search"
    description = (
        "Search the web for information about the subject. Returns a list of "
        "URLs with titles and snippets. Use broad and narrow queries to cover "
        "identity variants and known quantifiers (school, employer, city)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
        },
        "required": ["query"],
    }
    tier = "free"
    est_cost_usd_per_call = 0.004
    vendor = "tavily"
    direct_scraping = False
    internal_concurrency = None

    def __init__(self, client: AsyncTavilyClient | None = None):
        self._client = client

    @property
    def client(self) -> AsyncTavilyClient:
        if self._client is None:
            self._client = _get_client()
        return self._client

    async def run(self, query: str, max_results: int = 10, **_: Any) -> dict:
        raw = await self.client.search(query=query, max_results=max_results)
        return {"results": raw.get("results", []), "raw": raw}


class TavilyExtractTool:
    name = "tavily_extract"
    description = (
        "Fetch the cleaned content of one or more URLs. Use to read the actual "
        "page content behind a search hit before citing it as evidence."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 10,
            },
        },
        "required": ["urls"],
    }
    tier = "free"
    est_cost_usd_per_call = 0.001
    vendor = "tavily"
    direct_scraping = False
    internal_concurrency = None

    def __init__(self, client: AsyncTavilyClient | None = None):
        self._client = client

    @property
    def client(self) -> AsyncTavilyClient:
        if self._client is None:
            self._client = _get_client()
        return self._client

    async def run(self, urls: list[str], **_: Any) -> dict:
        raw = await self.client.extract(urls=urls)
        return {"results": raw.get("results", []), "raw": raw}
```

- [ ] **Step 4: Register in package** (no code change; we wire registrations in Task 14 so tests can import tool classes in isolation)

- [ ] **Step 5: Run tests — expect pass**

Run: `pytest tests/test_tools_tavily.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add osint/tools/tavily.py tests/test_tools_tavily.py
git commit -m "feat(tools): add tavily_search and tavily_extract"
```

---

## Task 10: Maigret tool (direct-scraping, with all knobs)

**Files:**
- Create: `osint/tools/maigret.py`
- Create: `tests/test_tools_maigret.py`

Maigret is the only v1 tool that hits target sites directly from our IP. It exposes `max_connections`, `timeout`, `proxy_url` (from `ScanConfig.tool_options["maigret"]`), and `sites_filter` knobs.

Maigret's Python API (from the `maigret` package) exposes `maigret.search(username, ...)` returning a dict keyed by site name. Because its signature has drifted between versions, we wrap calls defensively and pass only documented kwargs.

- [ ] **Step 1: Write failing tests**

Create `tests/test_tools_maigret.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from osint.tools.maigret import MaigretTool


async def test_maigret_calls_search_with_defaults(mocker):
    fake_search = AsyncMock(return_value={
        "GitHub": {"status": {"message": "Claimed"}, "url_user": "https://github.com/j"},
    })
    mocker.patch("osint.tools.maigret._search", fake_search)

    tool = MaigretTool()
    out = await tool.run(username="jdoe")

    fake_search.assert_awaited_once()
    kwargs = fake_search.call_args.kwargs
    assert kwargs["username"] == "jdoe"
    assert kwargs["max_connections"] == 15
    assert kwargs["timeout"] == 10
    assert kwargs.get("proxy") is None
    assert "found_accounts" in out
    assert out["found_accounts"][0]["site"] == "GitHub"
    assert "raw" in out


async def test_maigret_forwards_overrides(mocker):
    fake_search = AsyncMock(return_value={})
    mocker.patch("osint.tools.maigret._search", fake_search)
    tool = MaigretTool(proxy_url="http://p:8080")
    await tool.run(username="jdoe", max_connections=5, sites_filter=["GitHub", "Reddit"])
    kwargs = fake_search.call_args.kwargs
    assert kwargs["max_connections"] == 5
    assert kwargs["proxy"] == "http://p:8080"
    assert kwargs["site_list"] == ["GitHub", "Reddit"]


async def test_maigret_tool_metadata():
    tool = MaigretTool()
    assert tool.name == "maigret"
    assert tool.direct_scraping is True
    assert tool.internal_concurrency == 15
    assert "username" in tool.input_schema["properties"]
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_tools_maigret.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement `osint/tools/maigret.py`**

```python
import asyncio
from typing import Any

try:
    import maigret as _maigret_pkg
except ImportError:  # pragma: no cover
    _maigret_pkg = None


async def _search(
    *,
    username: str,
    max_connections: int,
    timeout: int,
    proxy: str | None,
    site_list: list[str] | None,
) -> dict:
    """Thin async wrapper around the maigret library.

    maigret's public entry point is synchronous and blocking; we offload to a
    thread. The function is extracted so tests can patch it cleanly.
    """
    if _maigret_pkg is None:
        raise RuntimeError("maigret is not installed")

    def _run() -> dict:
        # maigret exposes `search` in recent versions; older versions expose
        # `maigret.maigret.run`. Use whichever is available.
        fn = getattr(_maigret_pkg, "search", None) or getattr(
            _maigret_pkg.maigret, "search", None
        )
        if fn is None:
            raise RuntimeError("maigret.search entrypoint not found")
        return fn(
            username=username,
            max_connections=max_connections,
            timeout=timeout,
            proxy=proxy,
            site_list=site_list,
        )

    return await asyncio.to_thread(_run)


class MaigretTool:
    name = "maigret"
    description = (
        "Check ~3000 websites for the presence of a username. Use after you "
        "have a confirmed or likely username to map the subject's online "
        "footprint. Pass a `sites_filter` list to restrict the check."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "username": {"type": "string"},
            "max_connections": {"type": "integer", "minimum": 1, "maximum": 50, "default": 15},
            "timeout": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
            "sites_filter": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Restrict the check to this list of site names.",
            },
        },
        "required": ["username"],
    }
    tier = "free"
    est_cost_usd_per_call = 0.0
    vendor = "maigret"
    direct_scraping = True
    internal_concurrency = 15

    def __init__(self, proxy_url: str | None = None):
        self.proxy_url = proxy_url

    async def run(
        self,
        username: str,
        max_connections: int = 15,
        timeout: int = 10,
        sites_filter: list[str] | None = None,
        **_: Any,
    ) -> dict:
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
        return {"found_accounts": found, "raw": raw}
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_tools_maigret.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/tools/maigret.py tests/test_tools_maigret.py
git commit -m "feat(tools): add maigret with proxy, concurrency, sites_filter knobs"
```

---

## Task 11: Apify tools (`apify_instagram`, `apify_linkedin`)

**Files:**
- Create: `osint/tools/apify.py`
- Create: `tests/test_tools_apify.py`

Both tools call an Apify actor synchronously and block until completion. We use `apify-client`'s async `ApifyClientAsync`. Actor IDs are configurable at construction.

- [ ] **Step 1: Write failing tests**

Create `tests/test_tools_apify.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from osint.tools.apify import ApifyInstagramTool, ApifyLinkedInTool


def _fake_client():
    client = MagicMock()
    actor = MagicMock()
    client.actor = MagicMock(return_value=actor)
    dataset = MagicMock()
    client.dataset = MagicMock(return_value=dataset)
    actor.call = AsyncMock(return_value={"defaultDatasetId": "ds1"})
    dataset.list_items = AsyncMock(return_value=MagicMock(items=[{"username": "jdoe"}]))
    return client, actor, dataset


async def test_apify_instagram_calls_actor():
    client, actor, dataset = _fake_client()
    tool = ApifyInstagramTool(client=client, actor_id="apify~instagram-scraper")
    out = await tool.run(username="jdoe")
    actor.call.assert_awaited_once()
    dataset.list_items.assert_awaited_once()
    assert out["items"][0]["username"] == "jdoe"
    assert out["raw"]["default_dataset_id"] == "ds1"


async def test_apify_linkedin_by_profile_url():
    client, actor, dataset = _fake_client()
    dataset.list_items.return_value = MagicMock(items=[{"fullName": "Jane"}])
    tool = ApifyLinkedInTool(client=client, actor_id="apify~linkedin-profile-scraper")
    out = await tool.run(profile_url="https://www.linkedin.com/in/jane/")
    kwargs = actor.call.call_args.kwargs
    assert "run_input" in kwargs
    # Actor-specific input shape
    assert any("linkedin.com/in/jane" in str(v) for v in kwargs["run_input"].values())
    assert out["items"][0]["fullName"] == "Jane"


async def test_apify_instagram_metadata():
    tool = ApifyInstagramTool(client=MagicMock(), actor_id="x")
    assert tool.name == "apify_instagram"
    assert tool.tier == "paid"
    assert tool.direct_scraping is False
    assert "username" in tool.input_schema["properties"]


async def test_apify_linkedin_metadata():
    tool = ApifyLinkedInTool(client=MagicMock(), actor_id="x")
    assert tool.name == "apify_linkedin"
    assert tool.tier == "paid"
    assert tool.direct_scraping is False
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_tools_apify.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement `osint/tools/apify.py`**

```python
import os
from typing import Any

from apify_client import ApifyClientAsync

from osint.errors import ScanConfigError


DEFAULT_IG_ACTOR = "apify~instagram-scraper"
DEFAULT_LI_ACTOR = "apify~linkedin-profile-scraper"


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
    return {
        "items": items,
        "raw": {"default_dataset_id": dataset_id, "items": items},
    }


class ApifyInstagramTool:
    name = "apify_instagram"
    description = (
        "Fetch an Instagram user's public profile and recent posts via an Apify "
        "scraper. Use when you have a confirmed Instagram handle."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "username": {"type": "string", "description": "Instagram handle, without @."},
            "results_limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        },
        "required": ["username"],
    }
    tier = "paid"
    est_cost_usd_per_call = 0.15
    vendor = "apify"
    direct_scraping = False
    internal_concurrency = None

    def __init__(self, client: ApifyClientAsync | None = None, actor_id: str = DEFAULT_IG_ACTOR):
        self._client = client
        self.actor_id = actor_id

    @property
    def client(self) -> ApifyClientAsync:
        if self._client is None:
            self._client = _get_client()
        return self._client

    async def run(self, username: str, results_limit: int = 20, **_: Any) -> dict:
        return await _run_actor(self.client, self.actor_id, {
            "usernames": [username],
            "resultsLimit": results_limit,
        })


class ApifyLinkedInTool:
    name = "apify_linkedin"
    description = (
        "Fetch a LinkedIn profile via an Apify scraper. Requires the public "
        "profile URL. Returns positions, education, skills, and connections count."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "profile_url": {"type": "string", "description": "Full https://www.linkedin.com/in/... URL"},
        },
        "required": ["profile_url"],
    }
    tier = "paid"
    est_cost_usd_per_call = 0.05
    vendor = "apify"
    direct_scraping = False
    internal_concurrency = None

    def __init__(self, client: ApifyClientAsync | None = None, actor_id: str = DEFAULT_LI_ACTOR):
        self._client = client
        self.actor_id = actor_id

    @property
    def client(self) -> ApifyClientAsync:
        if self._client is None:
            self._client = _get_client()
        return self._client

    async def run(self, profile_url: str, **_: Any) -> dict:
        return await _run_actor(self.client, self.actor_id, {
            "profileUrls": [profile_url],
        })
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_tools_apify.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/tools/apify.py tests/test_tools_apify.py
git commit -m "feat(tools): add apify_instagram and apify_linkedin"
```

---

## Task 12: `grok_x_search` — X content via Grok Live Search

**Files:**
- Create: `osint/tools/grok_x.py`
- Create: `tests/test_tools_grok_x.py`

Reuses an `AsyncOpenAI` client pointed at xAI. The tool makes a *separate* Grok call from the main agent loop, with `search_parameters` in `extra_body` to scope to X.

- [ ] **Step 1: Write failing tests**

Create `tests/test_tools_grok_x.py`:

```python
from unittest.mock import AsyncMock, MagicMock

from osint.tools.grok_x import GrokXSearchTool


async def test_grok_x_search_calls_live_search():
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message = MagicMock(content="@jane posted about X last week")
    resp.model_dump.return_value = {"id": "r"}
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)

    tool = GrokXSearchTool(client=client, model="grok-4.20")
    out = await tool.run(query="jane doe on x", max_results=10)

    client.chat.completions.create.assert_awaited_once()
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "grok-4.20"
    sp = kwargs["extra_body"]["search_parameters"]
    assert sp["mode"] == "on"
    assert sp["sources"] == [{"type": "x"}]
    assert sp["max_search_results"] == 10
    assert out["answer"] == "@jane posted about X last week"
    assert out["raw"]["id"] == "r"


async def test_grok_x_metadata():
    tool = GrokXSearchTool(client=MagicMock())
    assert tool.name == "grok_x_search"
    assert tool.tier == "paid"
    assert tool.direct_scraping is False
    assert "query" in tool.input_schema["properties"]
```

- [ ] **Step 2: Run — expect failure**

Run: `pytest tests/test_tools_grok_x.py -v`
Expected: `ImportError`.

- [ ] **Step 3: Implement `osint/tools/grok_x.py`**

```python
import os
from typing import Any

from openai import AsyncOpenAI

from osint.errors import ScanConfigError


class GrokXSearchTool:
    name = "grok_x_search"
    description = (
        "Search X (formerly Twitter) for content relevant to a query using "
        "Grok's Live Search, scoped to X sources only. Use when you want X-native "
        "content (posts, profiles, quotes) rather than what Google indexes about X."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 30, "default": 15},
        },
        "required": ["query"],
    }
    tier = "paid"
    est_cost_usd_per_call = 0.05
    vendor = "xai"
    direct_scraping = False
    internal_concurrency = None

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "grok-4.20",
        base_url: str = "https://api.x.ai/v1",
        client: AsyncOpenAI | None = None,
    ):
        self.model = model
        if client is not None:
            self._client = client
        else:
            key = api_key or os.environ.get("XAI_API_KEY")
            if not key:
                raise ScanConfigError("XAI_API_KEY is not set")
            self._client = AsyncOpenAI(api_key=key, base_url=base_url)

    async def run(self, query: str, max_results: int = 15, **_: Any) -> dict:
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": query}],
            extra_body={
                "search_parameters": {
                    "mode": "on",
                    "sources": [{"type": "x"}],
                    "max_search_results": max_results,
                },
            },
        )
        return {
            "answer": resp.choices[0].message.content or "",
            "raw": resp.model_dump(),
        }
```

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_tools_grok_x.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/tools/grok_x.py tests/test_tools_grok_x.py
git commit -m "feat(tools): add grok_x_search using Grok Live Search scoped to X"
```

---

## Task 13: System prompts and report parsing

**Files:**
- Create: `osint/prompts.py`
- Create: `tests/test_prompts.py`

The agent's system prompt instructs Grok to (a) extract identifiers from the subject string, (b) use tools in parallel where independent, (c) emit its final answer as a JSON object wrapped in ```` ```json ```` fences with a fixed top-level shape: `{"extracted_identifiers": {...}, "report": {...}}`. `parse_report` extracts this from free-form model output, falling back to `{"extracted_identifiers": {}, "report": {"text": <raw>}}` on parse failure.

- [ ] **Step 1: Write failing tests**

Create `tests/test_prompts.py`:

```python
from osint.prompts import build_system_prompt, build_synthesis_prompt, parse_report


class _T:
    def __init__(self, name):
        self.name = name
        self.description = f"desc for {name}"


def test_system_prompt_contains_subject_and_tools():
    p = build_system_prompt(subject="Jane, NYC, @jdoe", tools=[_T("tavily_search"), _T("maigret")])
    assert "Jane, NYC, @jdoe" in p
    assert "tavily_search" in p
    assert "maigret" in p
    assert "extracted_identifiers" in p
    assert "```json" in p


def test_synthesis_prompt_mentions_stop_reason():
    p = build_synthesis_prompt(stop_reason="budget")
    assert "budget" in p.lower()
    assert "```json" in p


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
    text = '{"extracted_identifiers": {}, "report": {"x": 1}}'
    r = parse_report(text)
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
You are a self-OSINT agent. Your user wants to know what is publicly discoverable
about themselves online. The subject is the caller; the caller has consented.

SUBJECT DESCRIPTION (free-form, may include name, emails, handles, school,
employer, city, past addresses, and other identifiers):
---
{subject}
---

First, parse the description into a structured set of identifiers (emails,
phones, usernames, full name variants, schools, employers, cities, platform
URLs). Then use the tools below to investigate the subject.

Rules:
- Call multiple tools in the same turn when queries are independent.
- Prefer cheap, broad tools (tavily_search) first; then drill into specifics.
- Always extract what you can from raw search results before spending a paid
  tool's budget.
- Stop asking for tool calls when you have enough, or when nothing new is
  likely to surface.

Available tools:
{tool_catalog}

Final output:
When you are ready to finish, return ONLY a single assistant message with NO
tool calls, containing one fenced JSON block of this exact shape:

```json
{{
  "extracted_identifiers": {{ "emails": [...], "usernames": [...], "urls": [...], "...": "..." }},
  "report": {{
    "summary": "...",
    "accounts": [...],
    "web_presence": [...],
    "exposures": [...],
    "remediation": [...]
  }}
}}
```

The fenced JSON is the user-visible report. Put anything you want the user to
see there; the schema above is a guideline, not a rigid contract.
"""


SYNTHESIS_TEMPLATE = """\
The scan was cut short. Reason: {stop_reason}.

Based on the tool calls already made and their results in the conversation so
far, produce a final report. Return ONLY a fenced JSON block with the shape:

```json
{{
  "extracted_identifiers": {{...}},
  "report": {{...}}
}}
```
"""


def build_system_prompt(subject: str, tools: list) -> str:
    catalog = "\n".join(f"- {t.name}: {t.description}" for t in tools)
    return SYSTEM_TEMPLATE.format(subject=subject, tool_catalog=catalog)


def build_synthesis_prompt(stop_reason: str) -> str:
    return SYNTHESIS_TEMPLATE.format(stop_reason=stop_reason)


_FENCED_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def parse_report(text: str) -> dict:
    """Extract `{extracted_identifiers, report}` from model output.

    Tolerant: accepts a fenced ```json block, or a bare JSON object, or falls
    back to wrapping the raw text as `{report: {text: ...}}` so the scan still
    produces a usable artifact.
    """
    text = text or ""
    m = _FENCED_JSON.search(text)
    candidates = []
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
git commit -m "feat(prompts): add system/synthesis prompt builders and report parser"
```

---

## Task 14: `scan()` — the agent loop

**Files:**
- Create: `osint/scan.py`
- Create: `osint/log.py`
- Create: `tests/test_scan.py`

The `scan()` function wires everything together. This is the biggest task — split into smaller steps. We validate inputs, construct `ScanState`, seed messages with the system prompt, then loop: call LLM → if no tool_uses, parse and store the report; else dispatch tool_uses in parallel, append results to the message history, loop. On any non-final stop condition, run a synthesis call. Always write the scan JSON. Return `ScanResult`.

- [ ] **Step 1: Write the structlog setup**

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

- [ ] **Step 2: Write failing `scan()` tests**

Create `tests/test_scan.py`:

```python
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from osint.scan import scan
from osint.tools import REGISTRY
from osint.types import LLMResponse, ScanConfig, ToolUse


class _StubTool:
    name = "stub"
    description = "stub"
    input_schema = {"type": "object", "properties": {}}
    tier = "free"
    est_cost_usd_per_call = 0.01
    vendor = "none"
    direct_scraping = False
    internal_concurrency = None

    def __init__(self, side_effect=None):
        self.run = AsyncMock(side_effect=side_effect or (lambda **_: {"r": 1}))


@pytest.fixture(autouse=True)
def _clean_registry():
    REGISTRY.clear()
    yield
    REGISTRY.clear()


def _llm_stub(*turns):
    """Return an LLM whose .call() yields the given turns in order, then stops."""
    llm = MagicMock()
    llm.call = AsyncMock(side_effect=list(turns))
    llm.synthesize = AsyncMock(return_value='```json\n{"report": {"summary":"synth"}}\n```')
    return llm


async def test_scan_rejects_empty_subject(tmp_path):
    with pytest.raises(ValueError):
        await scan(subject="   ", config=ScanConfig(), llm=MagicMock(), scans_dir=tmp_path)


async def test_scan_happy_path(tmp_path):
    t = _StubTool()
    REGISTRY[t.name] = t
    llm = _llm_stub(
        LLMResponse(
            text="planning",
            tool_uses=[ToolUse(id="a", name="stub", input={})],
            assistant_message_raw={"role": "assistant", "content": "planning"},
        ),
        LLMResponse(
            text='```json\n{"extracted_identifiers":{"emails":["j@e"]},"report":{"summary":"hi"}}\n```',
            tool_uses=[],
            assistant_message_raw={"role": "assistant", "content": "done"},
        ),
    )
    result = await scan(
        subject="Jane Doe, j@e",
        config=ScanConfig(enabled_tools={"stub"}),
        llm=llm,
        scans_dir=tmp_path,
    )
    assert t.run.await_count == 1
    assert result.report == {"summary": "hi"}
    assert result.extracted_identifiers == {"emails": ["j@e"]}
    assert result.path.exists()
    assert llm.call.await_count == 2
    assert llm.synthesize.await_count == 0


async def test_scan_stops_on_budget(tmp_path):
    t = _StubTool()
    REGISTRY[t.name] = t
    # LLM keeps asking for tools; budget cap forces stop.
    tool_use_turn = LLMResponse(
        text="more",
        tool_uses=[ToolUse(id="a", name="stub", input={})],
        assistant_message_raw={"role": "assistant", "content": "more"},
    )
    llm = _llm_stub(tool_use_turn, tool_use_turn, tool_use_turn)
    result = await scan(
        subject="Jane",
        config=ScanConfig(enabled_tools={"stub"}, budget_usd=0.015),
        llm=llm,
        scans_dir=tmp_path,
    )
    # After 2 successful calls at $0.01 each, budget is exhausted. Synthesis runs.
    assert llm.synthesize.await_count == 1
    assert result.report == {"summary": "synth"}
    assert result.total_cost_usd >= 0.01


async def test_scan_parallel_tool_dispatch(tmp_path):
    import asyncio
    delays: list[float] = []

    async def slow(**_):
        import time
        start = time.monotonic()
        await asyncio.sleep(0.05)
        delays.append(time.monotonic() - start)
        return {"r": 1}

    t = _StubTool(side_effect=slow)
    REGISTRY[t.name] = t
    llm = _llm_stub(
        LLMResponse(
            text="fan out",
            tool_uses=[
                ToolUse(id=f"a{i}", name="stub", input={}) for i in range(4)
            ],
            assistant_message_raw={"role": "assistant", "content": "fan"},
        ),
        LLMResponse(
            text='```json\n{"report": {"summary":"done"}}\n```',
            tool_uses=[],
            assistant_message_raw={"role": "assistant", "content": "done"},
        ),
    )
    import time
    start = time.monotonic()
    await scan(
        subject="Jane",
        config=ScanConfig(enabled_tools={"stub"}),
        llm=llm,
        scans_dir=tmp_path,
    )
    elapsed = time.monotonic() - start
    # Four 0.05s calls dispatched in parallel should finish in well under 0.2s.
    assert elapsed < 0.15
    assert t.run.await_count == 4


async def test_scan_skips_unregistered_tool_requests(tmp_path):
    """If the LLM asks for a tool not in enabled_tools, skip with an error ToolCall."""
    t = _StubTool()
    REGISTRY[t.name] = t
    llm = _llm_stub(
        LLMResponse(
            text="oops",
            tool_uses=[ToolUse(id="a", name="not_registered", input={})],
            assistant_message_raw={"role": "assistant", "content": "oops"},
        ),
        LLMResponse(
            text='```json\n{"report":{"summary":"ok"}}\n```',
            tool_uses=[],
            assistant_message_raw={"role": "assistant"},
        ),
    )
    result = await scan(
        subject="Jane",
        config=ScanConfig(enabled_tools={"stub"}),
        llm=llm,
        scans_dir=tmp_path,
    )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].error is not None
    assert "not enabled" in result.tool_calls[0].error or "unknown" in result.tool_calls[0].error
```

- [ ] **Step 3: Implement `scan()`**

Create `osint/scan.py`:

```python
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osint.llm import LLM, GrokLLM
from osint.log import configure_logging, logger
from osint.prompts import build_synthesis_prompt, build_system_prompt, parse_report
from osint.state import ScanState, StopReason
from osint.storage import new_scan_id, write_scan_json
from osint.tools import REGISTRY, invoke_tool
from osint.types import LLMResponse, ScanConfig, ScanResult, ToolCall, ToolUse


async def scan(
    subject: str,
    config: ScanConfig = ScanConfig(),
    llm: LLM | None = None,
    scans_dir: Path = Path("./scans"),
) -> ScanResult:
    if not subject or not subject.strip():
        raise ValueError("subject must be a non-empty description")
    configure_logging()

    llm = llm or GrokLLM()
    tools = [REGISTRY[n] for n in sorted(config.enabled_tools) if n in REGISTRY]
    tool_map = {t.name: t for t in tools}
    state = ScanState(scan_id=new_scan_id(), subject=subject, config=config)

    logger.info("scan.start", scan_id=state.scan_id, enabled_tools=sorted(config.enabled_tools))

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(subject, tools)},
    ]

    turn = 0
    stop_reason: StopReason = StopReason.NONE
    try:
        while True:
            stopped, stop_reason = state.should_stop()
            if stopped:
                break

            response: LLMResponse = await llm.call(messages, tools=tools)
            messages.append(_to_openai_assistant_message(response))
            turn += 1

            if not response.tool_uses:
                parsed = parse_report(response.text)
                state.record_final_report(parsed["report"], identifiers=parsed["extracted_identifiers"])
                stop_reason = StopReason.FINAL_REPORT
                break

            tool_calls = await asyncio.gather(*[
                _dispatch(tu, tool_map, state, turn) for tu in response.tool_uses
            ])
            for tc in tool_calls:
                state.record_tool_call(tc)
                messages.append(_tool_result_message(tc))

        if not state.has_final_report():
            logger.info("scan.synthesize", scan_id=state.scan_id, stop_reason=stop_reason.value)
            messages.append({"role": "user", "content": build_synthesis_prompt(stop_reason.value)})
            synth_text = await llm.synthesize(messages)
            parsed = parse_report(synth_text)
            state.record_final_report(parsed["report"], identifiers=parsed["extracted_identifiers"])

        path = await write_scan_json(scans_dir, state, status="done")
        logger.info(
            "scan.done",
            scan_id=state.scan_id,
            tool_calls=len(state.tool_calls),
            cost_usd=state.total_cost_usd,
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
        # Still write what we have, so the scan is never lost.
        try:
            await write_scan_json(scans_dir, state, status="failed")
        except Exception:
            pass
        raise


async def _dispatch(tool_use: ToolUse, tool_map: dict, state: ScanState, turn: int) -> ToolCall:
    tool = tool_map.get(tool_use.name)
    if tool is None:
        now = datetime.now(timezone.utc)
        return ToolCall(
            turn=turn, tool=tool_use.name, input=tool_use.input,
            output=None, raw=None,
            started_at=now, completed_at=now,
            cost_usd=0.0,
            error=f"tool not enabled or unknown: {tool_use.name}",
        )
    return await invoke_tool(tool, tool_use, state, turn=turn)


def _to_openai_assistant_message(response: LLMResponse) -> dict:
    # Preserve tool_calls on the assistant message so the subsequent
    # tool result messages can reference them by tool_call_id.
    msg: dict[str, Any] = {"role": "assistant", "content": response.text or None}
    if response.tool_uses:
        msg["tool_calls"] = [
            {
                "id": tu.id,
                "type": "function",
                "function": {"name": tu.name, "arguments": json.dumps(tu.input)},
            }
            for tu in response.tool_uses
        ]
    return msg


def _tool_result_message(tc: ToolCall) -> dict:
    # OpenAI-compatible tool result message. tool_call_id matches the id
    # emitted in the assistant turn.
    if tc.error:
        content = json.dumps({"error": tc.error})
    else:
        content = json.dumps(tc.output or {}, default=str)
    return {
        "role": "tool",
        "tool_call_id": _tool_call_id_for(tc),
        "content": content,
    }


def _tool_call_id_for(tc: ToolCall) -> str:
    # ScanState doesn't retain tool_use.id on ToolCall (by design — it's a
    # vendor detail). Since results are appended in the same order they were
    # dispatched and the assistant message's tool_calls list matches that
    # order, we rely on positional matching at the OpenAI protocol level by
    # reusing the tool name + turn as a stable identifier. If the vendor
    # requires an exact id match we'd need to thread tool_use.id through.
    return f"{tc.tool}_{tc.turn}_{id(tc)}"
```

> **Implementation note on `_tool_call_id_for`:** OpenAI's wire protocol expects the `tool_call_id` in tool result messages to match the assistant message's `tool_calls[].id`. The cleanest fix is to thread `tool_use.id` through `invoke_tool` → `ToolCall`. Add a `tool_use_id: str | None = None` field to `ToolCall` in a follow-up refinement if integration tests show the protocol matching fails — Task 14.5 handles that.

- [ ] **Step 4: Run tests — expect pass**

Run: `pytest tests/test_scan.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/scan.py osint/log.py tests/test_scan.py
git commit -m "feat(scan): add agent loop with parallel tool dispatch and synthesis fallback"
```

---

## Task 14.5: Thread `tool_use_id` through `ToolCall` for protocol-correct tool results

**Why:** OpenAI's API requires `tool_call_id` on tool result messages to match `tool_calls[].id` from the assistant turn. The placeholder in Task 14 works structurally but breaks at runtime against the real xAI endpoint.

**Files:**
- Modify: `osint/types.py`
- Modify: `osint/tools/__init__.py`
- Modify: `osint/scan.py`
- Modify: `tests/test_types.py`
- Modify: `tests/test_tools_invoke.py`
- Modify: `tests/test_scan.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_tools_invoke.py`:

```python
async def test_invoke_tool_records_tool_use_id():
    t = _Stub()
    state = ScanState(scan_id="s", subject="x", config=ScanConfig())
    tu = ToolUse(id="call_abc", name="stub", input={})
    tc = await invoke_tool(t, tu, state, turn=3)
    assert tc.tool_use_id == "call_abc"
```

Run: `pytest tests/test_tools_invoke.py::test_invoke_tool_records_tool_use_id -v`
Expected: `AttributeError: ... tool_use_id`.

- [ ] **Step 2: Add `tool_use_id` to `ToolCall`**

In `osint/types.py`, add to `ToolCall`:

```python
class ToolCall(BaseModel):
    turn: int
    tool: str
    tool_use_id: str | None = None    # NEW
    input: dict[str, Any]
    output: dict[str, Any] | None
    raw: Any
    started_at: datetime
    completed_at: datetime
    cost_usd: float
    error: str | None = None
```

- [ ] **Step 3: Populate it in `invoke_tool` and the unknown-tool branch of `_dispatch`**

In `osint/tools/__init__.py`, change `invoke_tool` to construct `ToolCall` with `tool_use_id=tool_use.id`. In `osint/scan.py` `_dispatch`, also pass `tool_use_id=tool_use.id` in the unknown-tool branch.

- [ ] **Step 4: Use it in `_tool_result_message` and drop the placeholder `_tool_call_id_for`**

In `osint/scan.py`, delete `_tool_call_id_for` and inline:

```python
def _tool_result_message(tc: ToolCall) -> dict:
    if tc.error:
        content = json.dumps({"error": tc.error})
    else:
        content = json.dumps(tc.output or {}, default=str)
    return {
        "role": "tool",
        "tool_call_id": tc.tool_use_id or f"{tc.tool}_{tc.turn}",
        "content": content,
    }
```

- [ ] **Step 5: Run all tests — expect pass**

Run: `pytest -v`
Expected: all tests pass (existing ones tolerate the new optional field).

- [ ] **Step 6: Commit**

```bash
git add osint/types.py osint/tools/__init__.py osint/scan.py tests/test_tools_invoke.py
git commit -m "fix(scan): thread tool_use_id through ToolCall for OpenAI-compatible tool results"
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
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from osint.cli import main


async def test_cli_passes_subject_to_scan(tmp_path: Path, capsys):
    fake_result = type("R", (), {})()
    fake_result.scan_id = "sid"
    fake_result.path = tmp_path / "sid.json"
    (tmp_path / "sid.json").write_text("{}")

    with patch("osint.cli.scan", new=AsyncMock(return_value=fake_result)) as m:
        await main(["scan", "Jane Doe, jane@e, @jdoe", "--scans-dir", str(tmp_path)])
    assert m.await_count == 1
    kwargs = m.call_args.kwargs
    assert kwargs["subject"] == "Jane Doe, jane@e, @jdoe"
    assert kwargs["scans_dir"] == tmp_path


async def test_cli_reads_stdin_when_no_arg(tmp_path: Path, monkeypatch):
    fake_result = type("R", (), {})()
    fake_result.scan_id = "sid"
    fake_result.path = tmp_path / "sid.json"
    (tmp_path / "sid.json").write_text("{}")

    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("Jane from stdin"))
    with patch("osint.cli.scan", new=AsyncMock(return_value=fake_result)) as m:
        await main(["scan", "--scans-dir", str(tmp_path)])
    assert m.await_count == 1
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
                   help="Enable a tool by name. Can be repeated. Defaults to the "
                        "standard free set if omitted.")
    return s and parser


async def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd != "scan":
        parser.print_help()
        return 2

    subject = args.subject
    if subject is None:
        subject = sys.stdin.read()
    if not subject or not subject.strip():
        print("error: subject must be a non-empty description", file=sys.stderr)
        sys.exit(2)

    config_kwargs: dict = {
        "budget_usd": args.budget_usd,
        "max_tool_calls": args.max_calls,
        "max_wall_clock_sec": args.max_seconds,
    }
    if args.enable:
        config_kwargs["enabled_tools"] = set(args.enable)

    result = await scan(
        subject=subject,
        config=ScanConfig(**config_kwargs),
        scans_dir=args.scans_dir,
    )
    print(result.path)
    return 0


def _entry() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    _entry()
```

- [ ] **Step 4: Export from `osint/__init__.py`**

Overwrite `osint/__init__.py`:

```python
from osint.errors import ScanConfigError, ToolError
from osint.llm import LLM, GrokLLM
from osint.scan import scan
from osint.types import ScanConfig, ScanResult, ToolCall, ToolUse

# Register built-in tools so they become available by name.
from osint.tools import REGISTRY, register
from osint.tools.tavily import TavilyExtractTool, TavilySearchTool
from osint.tools.maigret import MaigretTool
from osint.tools.apify import ApifyInstagramTool, ApifyLinkedInTool
from osint.tools.grok_x import GrokXSearchTool


def _register_builtins() -> None:
    for cls in (
        TavilySearchTool, TavilyExtractTool,
        MaigretTool,
        ApifyInstagramTool, ApifyLinkedInTool,
        GrokXSearchTool,
    ):
        instance = cls()
        if instance.name not in REGISTRY:
            register(instance)


_register_builtins()

__all__ = [
    "scan",
    "ScanConfig",
    "ScanResult",
    "ToolCall",
    "ToolUse",
    "LLM",
    "GrokLLM",
    "ScanConfigError",
    "ToolError",
    "REGISTRY",
]
```

- [ ] **Step 5: Run all tests — expect all pass**

Run: `pytest -v`
Expected: all tests pass.

> **Caveat on test isolation.** `osint/__init__.py` registers built-in tools on import, but several tool constructors require env vars (`TAVILY_API_KEY`, `APIFY_TOKEN`, `XAI_API_KEY`) when no client is passed. For `_register_builtins()` to work in a dev environment without those env vars, each tool's constructor must allow lazy client creation (the Task 9/11/12 implementations already do — the client is only created on first `.run()` call, not at construction time). `MaigretTool` has no API key. `GrokXSearchTool` DOES require `XAI_API_KEY` at construction — wrap its registration in a try/except ImportError/ScanConfigError so importing `osint` without keys doesn't fail. Update `_register_builtins`:

```python
def _register_builtins() -> None:
    for cls in (
        TavilySearchTool, TavilyExtractTool,
        MaigretTool,
        ApifyInstagramTool, ApifyLinkedInTool,
        GrokXSearchTool,
    ):
        try:
            instance = cls()
        except ScanConfigError:
            continue   # tool needs env var; user must set it then reimport or register manually
        if instance.name not in REGISTRY:
            register(instance)
```

Also update `GrokXSearchTool.__init__` in `osint/tools/grok_x.py` to match the lazy pattern: defer key check until first `.run()`:

```python
def __init__(self, api_key: str | None = None, model: str = "grok-4.20",
             base_url: str = "https://api.x.ai/v1", client: AsyncOpenAI | None = None):
    self.model = model
    self._client = client
    self._api_key = api_key
    self._base_url = base_url

@property
def client(self) -> AsyncOpenAI:
    if self._client is None:
        key = self._api_key or os.environ.get("XAI_API_KEY")
        if not key:
            raise ScanConfigError("XAI_API_KEY is not set")
        self._client = AsyncOpenAI(api_key=key, base_url=self._base_url)
    return self._client
```

And in `.run()` use `self.client` instead of `self._client`. Re-run `pytest tests/test_tools_grok_x.py -v` — both tests should still pass since they inject a client.

- [ ] **Step 6: Commit**

```bash
git add osint/cli.py osint/__init__.py osint/tools/grok_x.py tests/test_cli.py
git commit -m "feat(cli): add argparse CLI and register built-in tools on import"
```

---

## Self-review

**Spec coverage check (§§1–11 of the spec):**

- §4 in-scope items: covered — async `scan()` (Task 14), single LLM vendor (Task 6), six tools (Tasks 9–12), JSON-per-scan (Task 5), tool-level concurrency caps (Task 8), budget/call/wall-clock caps (Tasks 2, 4).
- §5 architecture: fully materialized across Tasks 4–14.
- §6.1 free-form string input with empty-rejection: Tasks 14 (validation) and 15 (CLI stdin fallback).
- §6.2 agent loop, parallel dispatch, stop conditions: Task 14 + test `test_scan_parallel_tool_dispatch`.
- §6.3 Tool Protocol and registry: Task 7.
- §6.4 LLM Protocol and `GrokLLM`: Task 6.
- §6.5 `grok_x_search` as separate tool with Live Search scoped to X: Task 12.
- §6.6 Maigret mitigations — internal concurrency default 15, `TOOL_LIMITS` semaphore, `proxy_url`, `sites_filter`: Tasks 8 (semaphore) + 10 (knobs).
- §6.7 JSON output shape including `extracted_identifiers` + raw tool calls: Task 5 + Task 13 (report parse).
- §6.8 cap enforcement with a final synthesis pass: Task 14 (synthesis branch).
- §6.9 structlog logging keyed by `scan_id`, no subject PII: Task 14 (log.py + structured calls in `scan()`). *Note: the log calls included emit scan_id, tool count, cost, duration — never subject text. Tool-call start/end lines are not separately logged in the current plan; acceptable for v1, but if fine-grained per-tool traces are wanted, a follow-up would add them inside `invoke_tool`.*
- §7 v1 tool list: all six present.
- §8 public API including `python -m osint.cli`: Task 15.
- §9 env-var requirements and `ScanConfigError`: Tasks 9, 11, 12 (lazy-client pattern), 15 (tolerant registration).

**Placeholder scan:** one intentional note in Task 14 (the protocol id issue) is resolved by the dedicated Task 14.5. No "TBD"/"TODO"/"implement later" markers. All code steps include actual code.

**Type consistency:** `ToolCall` has `tool_use_id` added in Task 14.5; no downstream task references a field by a different name. `StopReason` is used consistently between `ScanState.should_stop()` (Task 4) and `scan()` (Task 14). `Tool` Protocol attributes (`direct_scraping`, `internal_concurrency`, `vendor`, etc.) used in Task 8's `invoke_tool` match the definitions in Task 7 and every tool implementation in Tasks 9–12.

**Scope check:** one plan, one subsystem (the library). Total ~15 tasks producing ~1,500 lines of code + ~800 lines of tests.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-24-self-osint-v1-agent.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
