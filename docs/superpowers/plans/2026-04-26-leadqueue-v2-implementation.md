# Lead-Queue v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second agent runner (`leadqueue_v2`) that performs investigation via a priority queue of leads with iterative verifier loop, while preserving the current ReAct agent (`react_v1`) verbatim.

**Architecture:** Two coexisting agent sub-packages under `osint/agents/`. The `osint/run.py` becomes a thin dispatcher routing to one or the other based on `ScanConfig.agent_version`. v2 uses a 5-stage pipeline (seed → main loop → synthesize → verifier loop → final report) where leads expand dynamically; each lead is processed by a small ReAct mini-loop and may emit findings + new leads. Synthesizer reads all findings; verifier can push more leads onto the queue and re-synthesize.

**Tech Stack:** Python 3.11, Pydantic 2, LangGraph (`create_react_agent`), LangChain Core (`BaseMessage`, `BaseChatModel`), pytest with `pytest-asyncio`, `BindableFakeModel` (existing test helper).

---

## File Structure

**New files (created):**
| Path | Purpose |
| --- | --- |
| `osint/agents/__init__.py` | AGENTS registry |
| `osint/agents/base.py` | `AgentRunner` Protocol + shared `RunOutcome` type |
| `osint/agents/react_v1/__init__.py` | exports `ReactV1Runner` |
| `osint/agents/react_v1/runner.py` | v1 orchestration (moved from `osint/run.py`) |
| `osint/agents/react_v1/prompts.py` | moved from `osint/prompts.py` |
| `osint/agents/leadqueue_v2/__init__.py` | exports `LeadQueueV2Runner` |
| `osint/agents/leadqueue_v2/queue.py` | `Lead`, `Source`, `Finding`, `LeadQueue` |
| `osint/agents/leadqueue_v2/processor.py` | `process_one_lead` |
| `osint/agents/leadqueue_v2/synthesizer.py` | `synthesize` |
| `osint/agents/leadqueue_v2/verifier.py` | `verify` |
| `osint/agents/leadqueue_v2/runner.py` | `LeadQueueV2Runner` glue |
| `osint/agents/leadqueue_v2/prompts.py` | processor/synthesizer/verifier prompts |
| `tests/agents/__init__.py` | (empty) |
| `tests/agents/test_base.py` | AgentRunner protocol tests |
| `tests/agents/leadqueue_v2/__init__.py` | (empty) |
| `tests/agents/leadqueue_v2/test_queue.py` | Lead/LeadQueue tests |
| `tests/agents/leadqueue_v2/test_processor.py` | processor tests |
| `tests/agents/leadqueue_v2/test_synthesizer.py` | synthesizer tests |
| `tests/agents/leadqueue_v2/test_verifier.py` | verifier tests |
| `tests/agents/leadqueue_v2/test_runner.py` | end-to-end integration test |
| `tests/test_dispatcher.py` | dispatcher routing test |

**Modified files:**
| Path | Change |
| --- | --- |
| `osint/run.py` | becomes thin dispatcher |
| `osint/state.py` | add `findings`, `leads_log`, `verifier_iterations` fields |
| `osint/types.py` | add `agent_version`, `max_verifier_iterations` to `ScanConfig` |
| `osint/cli.py` | add `--agent` flag |
| `tests/test_prompts.py` | update import path to `osint.agents.react_v1.prompts` |
| `tests/test_run.py` | imports updated; remaining tests cover the dispatcher's setup/teardown |
| `tests/test_types.py` | assert new `ScanConfig` defaults |

**Deleted:**
- `osint/prompts.py` (after content moves to `osint/agents/react_v1/prompts.py`)

---

## Phase A — Refactor (no behavior change, all existing tests stay green)

### Task A1: Create `agents/base.py` with `AgentRunner` protocol

**Files:**
- Create: `osint/agents/__init__.py`
- Create: `osint/agents/base.py`
- Create: `tests/agents/__init__.py`
- Create: `tests/agents/test_base.py`

- [ ] **Step 1: Create empty package init files**

```bash
mkdir -p osint/agents
mkdir -p tests/agents
touch osint/agents/__init__.py
touch tests/agents/__init__.py
```

- [ ] **Step 2: Write `osint/agents/base.py`**

```python
# osint/agents/base.py
"""Agent-runner protocol shared by v1 (ReAct) and v2 (lead-queue).

Each agent runner takes a fully-prepared scan context (subject, config,
LLM, ScanState, CappedTools, cost callback) and produces a parsed
report + an optional StopReason. Persistence (writing scan JSON/MD) is
handled by the dispatcher in osint/run.py — runners do NOT write files.
"""
from __future__ import annotations

from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel

from osint.state import ScanState, StopReason


class AgentRunner(Protocol):
    """Stable surface every agent version must implement."""

    async def run(
        self,
        *,
        subject: str,
        state: ScanState,
        llm: BaseChatModel,
        tools: list[Any],
        cost_cb: Any,
    ) -> tuple[dict, StopReason | None]:
        """Run the agent.

        Returns a 2-tuple:
          - parsed_report: {"extracted_identifiers": {...}, "report": {...}}
          - stop_reason:   StopReason | None  (None on a normal finish)

        Side effects: mutates `state` (records tool calls, messages, etc.).
        Persistence: caller's responsibility.
        """
        ...
```

- [ ] **Step 3: Write `tests/agents/test_base.py`**

```python
# tests/agents/test_base.py
"""Smoke test for the AgentRunner Protocol — confirms the module imports
and the symbol exists. Concrete runner conformance is covered in each
agent's own test module."""

def test_agent_runner_protocol_importable():
    from osint.agents.base import AgentRunner
    assert AgentRunner is not None
    # Protocol has the expected method
    assert "run" in AgentRunner.__dict__ or hasattr(AgentRunner, "run")
```

- [ ] **Step 4: Run tests, expect green**

```bash
source .venv/bin/activate
pytest tests/agents/test_base.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Run full suite, expect no regressions**

```bash
pytest tests/ -q
```

Expected: same pass count as before (94 or 95).

- [ ] **Step 6: Commit**

```bash
git add osint/agents/__init__.py osint/agents/base.py tests/agents/__init__.py tests/agents/test_base.py
git commit -m "refactor(agents): add osint.agents package with AgentRunner protocol"
```

---

### Task A2: Move `osint/prompts.py` → `osint/agents/react_v1/prompts.py`

**Files:**
- Create: `osint/agents/react_v1/__init__.py`
- Create: `osint/agents/react_v1/prompts.py` (content from `osint/prompts.py`)
- Delete: `osint/prompts.py`
- Modify: `tests/test_prompts.py` (import path)
- Modify: `osint/run.py` (import path) — temporary, will be re-touched in A4

- [ ] **Step 1: Create `osint/agents/react_v1/` and copy prompts**

```bash
mkdir -p osint/agents/react_v1
touch osint/agents/react_v1/__init__.py
cp osint/prompts.py osint/agents/react_v1/prompts.py
```

- [ ] **Step 2: Update import in `osint/run.py`**

In `osint/run.py`, find the line:

```python
from osint.prompts import (
    build_deepen_prompt,
    build_synthesis_prompt,
    build_system_prompt,
    format_tool_calls_for_synthesis,
    parse_report,
)
```

Replace `osint.prompts` with `osint.agents.react_v1.prompts`:

```python
from osint.agents.react_v1.prompts import (
    build_deepen_prompt,
    build_synthesis_prompt,
    build_system_prompt,
    format_tool_calls_for_synthesis,
    parse_report,
)
```

- [ ] **Step 3: Update import in `tests/test_prompts.py`**

```python
# tests/test_prompts.py — line 1
from osint.agents.react_v1.prompts import (
    build_deepen_prompt,
    build_system_prompt,
    build_synthesis_prompt,
    format_tool_calls_for_synthesis,
    parse_report,
)
```

- [ ] **Step 4: Search for any other importers**

```bash
grep -rn "from osint.prompts\|import osint.prompts" osint/ tests/
```

Expected: zero results after step 2 + 3. If anything appears, update each import to `osint.agents.react_v1.prompts`.

- [ ] **Step 5: Delete the old file**

```bash
rm osint/prompts.py
```

- [ ] **Step 6: Run full test suite, expect green**

```bash
pytest tests/ -q
```

Expected: same pass count as before. If anything fails on `ModuleNotFoundError`, you missed an import in step 4.

- [ ] **Step 7: Commit**

```bash
git add osint/agents/react_v1/__init__.py osint/agents/react_v1/prompts.py osint/run.py tests/test_prompts.py
git rm osint/prompts.py
git commit -m "refactor(agents): move prompts.py into osint/agents/react_v1/"
```

---

### Task A3: Move v1 runner logic into `osint/agents/react_v1/runner.py`

**Files:**
- Create: `osint/agents/react_v1/runner.py` (extracted from `osint/run.py`)
- Modify: `osint/run.py` (becomes dispatcher; keep `scan` exported)

- [ ] **Step 1: Read the current `osint/run.py`**

```bash
cat osint/run.py | head -30
```

Note the public surface: `scan(subject, config, llm, scans_dir)`. That stays in `osint/run.py`. The internals — `_run_one_pass`, `_synthesize`, `_extract_final_text`, `_serialize_messages`, `_merge_identifiers`, `_default_llm`, `create_react_agent` import — move into the new runner.

- [ ] **Step 2: Create `osint/agents/react_v1/runner.py`**

The full file. Copy ALL helper functions and the multi-pass loop body from the current `osint/run.py`, wrap them into a `ReactV1Runner` class implementing `AgentRunner`. The dispatcher (osint/run.py) will still own scan-id generation, ScanState construction, tool building, cost-callback, write_scan_json/markdown, and the failure-write path. The runner only owns the agent loop.

```python
# osint/agents/react_v1/runner.py
# API notes (verified 2026-04-23 against langgraph==1.1.9 / langchain-core):
# (Move the existing osint/run.py docstring lines 1-23 here verbatim.)

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from osint.errors import ScanStopped
from osint.llm_cost import LLMCostCallback
from osint.log import logger
from osint.agents.react_v1.prompts import (
    build_deepen_prompt,
    build_synthesis_prompt,
    build_system_prompt,
    format_tool_calls_for_synthesis,
    parse_report,
)
from osint.state import ScanState, StopReason
from osint.types import ScanConfig


# --- helpers (moved verbatim from osint/run.py) ---

async def _synthesize(
    llm: BaseChatModel,
    subject: str,
    state: ScanState,
    stop_reason: str,
    cost_cb: LLMCostCallback,
) -> tuple[str, list[Any]]:
    # ... copy from osint/run.py:74-93
    ...


def _extract_final_text(agent_result: dict) -> str:
    # ... copy from osint/run.py:96-102
    ...


def _serialize_messages(messages: list) -> list[dict]:
    # ... copy from osint/run.py:105-120
    ...


def _merge_identifiers(prev: dict, new: dict) -> dict:
    # ... copy from osint/run.py:123-150
    ...


async def _run_one_pass(
    *,
    pass_num: int,
    total_passes: int,
    subject: str,
    state: ScanState,
    llm: BaseChatModel,
    cost_cb: LLMCostCallback,
    tools: list,
    config: ScanConfig,
    previous_report_text: str | None,
) -> tuple[dict, StopReason | None]:
    # ... copy from osint/run.py:153-247 (the function body that orchestrates one pass)
    ...


# --- runner class ---

class ReactV1Runner:
    """v1 agent: single LangGraph create_react_agent loop, multi-pass deepen.

    Implements osint.agents.base.AgentRunner. Stateless across runs;
    each call to .run() builds and tears down its own LangGraph agent.
    """

    async def run(
        self,
        *,
        subject: str,
        state: ScanState,
        llm: BaseChatModel,
        tools: list,
        cost_cb: LLMCostCallback,
    ) -> tuple[dict, StopReason | None]:
        config = state.config
        previous_report_text: str | None = None
        last_parsed: dict = {"extracted_identifiers": {}, "report": {"text": ""}}
        last_stop: StopReason | None = None
        for pass_num in range(1, config.passes + 1):
            logger.info(
                "scan.pass.start",
                scan_id=state.scan_id,
                pass_num=pass_num,
                total_passes=config.passes,
            )
            parsed, pass_stop_reason = await _run_one_pass(
                pass_num=pass_num,
                total_passes=config.passes,
                subject=subject,
                state=state,
                llm=llm,
                cost_cb=cost_cb,
                tools=tools,
                config=config,
                previous_report_text=previous_report_text,
            )
            # Carry the multi-pass logic from osint/run.py here, including
            # the _merge_identifiers union-merge and the cap-cut break.
            # (Faithfully reproduce the existing pass-loop body.)
            ...
            last_parsed = parsed
            last_stop = pass_stop_reason
            if pass_stop_reason is not None:
                break
            previous_report_text = parsed.get("report", {}).get("text") or ""
        return last_parsed, last_stop
```

> **Note for the implementer:** This is a faithful extraction of the existing `osint/run.py`. The complete reference is the current contents of `osint/run.py` lines 1-247. Copy every function body verbatim; the only changes are: (a) the `prompts` import path is now the moved module, (b) the multi-pass loop body that lived inside `scan()` becomes the body of `ReactV1Runner.run`, (c) drop the `_default_llm`, `new_scan_id`, `write_scan_json`, `write_scan_markdown`, ScanState construction, and the failure-write path — those stay in the dispatcher.

- [ ] **Step 3: Rewrite `osint/run.py` as a thin dispatcher**

```python
# osint/run.py
"""Scan dispatcher. Dispatches to an agent runner based on
config.agent_version.

Keeps the public surface (`scan(subject, config, llm, scans_dir)` async
function returning ScanResult) — every existing CLI invocation continues
to work unchanged."""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from osint.agents import AGENTS
from osint.errors import ScanConfigError
from osint.llm_cost import LLMCostCallback
from osint.log import configure_logging, logger
from osint.state import ScanState
from osint.storage import new_scan_id, write_scan_json, write_scan_markdown
from osint.tools import build_tools
from osint.types import ScanConfig, ScanResult


def _default_llm(cfg: ScanConfig) -> ChatOpenAI:
    """Build the main agent LLM from a ScanConfig.

    `ChatOpenAI` accepts any OpenAI Chat Completions-compatible endpoint via
    `base_url`. This makes the LLM swappable across vendors (xAI, OpenAI,
    DeepSeek, Together, Groq, Ollama, vLLM, ...) without changing any of
    the rest of the pipeline.
    """
    key = os.environ.get(cfg.llm.api_key_env_var)
    if not key:
        raise ScanConfigError(
            f"{cfg.llm.api_key_env_var} is not set "
            f"(required by LLM model '{cfg.llm.model}' at {cfg.llm.base_url})"
        )
    return ChatOpenAI(
        model=cfg.llm.model,
        base_url=cfg.llm.base_url,
        api_key=key,
    )


async def scan(
    subject: str,
    config: ScanConfig = ScanConfig(),
    llm: BaseChatModel | None = None,
    scans_dir: Path = Path("./scans"),
) -> ScanResult:
    if not subject or not subject.strip():
        raise ValueError("subject must be a non-empty description")
    configure_logging()

    llm = llm or _default_llm(config)
    state = ScanState(scan_id=new_scan_id(), subject=subject, config=config)
    logger.info(
        "scan.start",
        scan_id=state.scan_id,
        enabled_tools=sorted(config.enabled_tools),
        passes=config.passes,
        agent_version=config.agent_version,
    )

    try:
        tools = build_tools(config, state)
        cost_cb = LLMCostCallback(state)

        if config.agent_version not in AGENTS:
            raise ScanConfigError(
                f"unknown agent_version: {config.agent_version!r}; "
                f"known: {sorted(AGENTS)}"
            )
        runner = AGENTS[config.agent_version]()

        parsed, stop_reason = await runner.run(
            subject=subject,
            state=state,
            llm=llm,
            tools=tools,
            cost_cb=cost_cb,
        )

        state.record_final_report(
            parsed.get("extracted_identifiers", {}),
            parsed.get("report", {}),
            stop_reason,
        )
        result = state.to_result(scans_dir=scans_dir)
        write_scan_json(result, scans_dir)
        write_scan_markdown(result, scans_dir)
        logger.info(
            "scan.done",
            scan_id=state.scan_id,
            duration_sec=state.duration_sec,
            llm_cost_usd=state.llm_cost_usd,
            llm_input_tokens=state.llm_input_tokens,
            llm_output_tokens=state.llm_output_tokens,
            tool_cost_usd=state.tool_cost_usd,
            total_cost_usd=state.total_cost_usd,
            tool_calls=len(state.tool_calls),
        )
        return result
    except Exception as e:
        # Record failure to disk before re-raising so partial state isn't lost.
        state.record_failure(repr(e))
        result = state.to_result(scans_dir=scans_dir)
        write_scan_json(result, scans_dir)
        raise
```

> **Note for the implementer:** Carry over any helper from the existing `osint/run.py` that the dispatcher still needs (e.g. `state.record_final_report`, `state.record_failure`, `state.to_result`). Do NOT re-implement persistence logic — `write_scan_json` and `write_scan_markdown` already exist in `osint/storage.py`.

- [ ] **Step 4: Wire ReactV1Runner into the registry**

```python
# osint/agents/__init__.py
"""Agent runner registry. Map agent_version (str) → AgentRunner class."""
from osint.agents.react_v1 import ReactV1Runner

AGENTS = {
    "react_v1": ReactV1Runner,
}
```

```python
# osint/agents/react_v1/__init__.py
from osint.agents.react_v1.runner import ReactV1Runner

__all__ = ["ReactV1Runner"]
```

- [ ] **Step 5: Run all tests, expect green**

```bash
pytest tests/ -q
```

Expected: same pass count as before this task (no regressions).

- [ ] **Step 6: Commit**

```bash
git add osint/agents/__init__.py osint/agents/react_v1/__init__.py osint/agents/react_v1/runner.py osint/run.py
git commit -m "refactor(agents): extract v1 logic into osint.agents.react_v1.ReactV1Runner; run.py becomes dispatcher"
```

---

### Task A4: Add dispatcher unit test

**Files:**
- Create: `tests/test_dispatcher.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_dispatcher.py
"""Dispatcher routing — confirms `scan()` looks up the agent runner via
the AGENTS registry by `config.agent_version`. The runner is mocked so
this test doesn't double-cover ReactV1Runner's loop logic (already
covered by tests/test_run.py)."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from osint.run import scan
from osint.types import ScanConfig


@pytest.fixture(autouse=True)
def _apify_env(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test")


async def test_dispatcher_routes_to_react_v1(tmp_path, monkeypatch):
    """agent_version='react_v1' (default) routes to ReactV1Runner.

    Patch the registry so we can assert the lookup happened with the
    right key without exercising the real runner's tool-building / loop."""
    fake_runner_cls = MagicMock()
    fake_runner_instance = MagicMock()
    fake_runner_instance.run = AsyncMock(
        return_value=({"extracted_identifiers": {}, "report": {"text": "ok"}}, None)
    )
    fake_runner_cls.return_value = fake_runner_instance

    monkeypatch.setitem(__import__("osint.agents", fromlist=["AGENTS"]).AGENTS,
                        "react_v1", fake_runner_cls)

    cfg = ScanConfig(enabled_tools={"web_search"})
    await scan(subject="Jane", config=cfg, llm=MagicMock(), scans_dir=tmp_path)
    fake_runner_cls.assert_called_once_with()
    fake_runner_instance.run.assert_awaited_once()


async def test_dispatcher_rejects_unknown_agent_version(tmp_path):
    from osint.errors import ScanConfigError
    cfg = ScanConfig(enabled_tools={"web_search"}, agent_version="does_not_exist")
    with pytest.raises(ScanConfigError, match="unknown agent_version"):
        await scan(subject="Jane", config=cfg, llm=MagicMock(), scans_dir=tmp_path)
```

> **Note for the implementer:** the second test references `agent_version` on `ScanConfig`. Until Task B1 adds that field, the test will fail because `ScanConfig` doesn't accept it. That's fine — leave the test in place; Task B1's tests will green it. If you want a passing test BEFORE Task B1, comment out the second test and uncomment after B1.

- [ ] **Step 2: Run the first test, expect green**

```bash
pytest tests/test_dispatcher.py::test_dispatcher_routes_to_react_v1 -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_dispatcher.py
git commit -m "test(dispatcher): assert agent_version routing via AGENTS registry"
```

---

## Phase B — Configuration plumbing

### Task B1: Add `agent_version` and `max_verifier_iterations` to `ScanConfig`

**Files:**
- Modify: `osint/types.py` (add fields to `ScanConfig`)
- Modify: `tests/test_types.py` (assert new defaults)

- [ ] **Step 1: Write the failing test**

In `tests/test_types.py`, find `test_scanconfig_defaults` and add assertions:

```python
def test_scanconfig_defaults():
    c = ScanConfig()
    assert c.enabled_tools == {"web_search", "web_extract", "maigret"}
    # ... existing assertions
    # NEW:
    assert c.agent_version == "react_v1"
    assert c.max_verifier_iterations == 3
```

- [ ] **Step 2: Run, expect fail**

```bash
pytest tests/test_types.py::test_scanconfig_defaults -v
```

Expected: AssertionError on `c.agent_version` (attribute does not exist) or ValidationError if extra fields are forbidden.

- [ ] **Step 3: Add the fields to `ScanConfig`**

In `osint/types.py`, find the `ScanConfig` class and add:

```python
from typing import Literal  # if not already imported

class ScanConfig(BaseModel):
    # ... existing fields
    agent_version: Literal["react_v1", "leadqueue_v2"] = "react_v1"
    max_verifier_iterations: PositiveInt = 3
```

- [ ] **Step 4: Run again, expect green**

```bash
pytest tests/test_types.py -v
```

- [ ] **Step 5: Commit**

```bash
git add osint/types.py tests/test_types.py
git commit -m "feat(types): add agent_version + max_verifier_iterations to ScanConfig"
```

---

### Task B2: Add `--agent` CLI flag + uncomment the dispatcher rejection test

**Files:**
- Modify: `osint/cli.py` (new flag + wire to ScanConfig)
- Modify: `tests/test_dispatcher.py` (un-skip the rejection test if you skipped it)
- Modify: `tests/test_cli.py` (add a flag-parsing test)

- [ ] **Step 1: Write CLI test for `--agent`**

In `tests/test_cli.py`, add:

```python
def test_cli_agent_flag_overrides_default(monkeypatch):
    """--agent leadqueue_v2 sets ScanConfig.agent_version."""
    from osint.cli import _build_args
    args = _build_args(["scan", "Jane", "--agent", "leadqueue_v2"])
    assert args.agent == "leadqueue_v2"


def test_cli_agent_flag_default_is_react_v1(monkeypatch):
    from osint.cli import _build_args
    args = _build_args(["scan", "Jane"])
    assert args.agent == "react_v1"
```

> **Note:** if `osint/cli.py` doesn't have a `_build_args` helper, factor the existing `argparse` setup into one (parser is constructed; `parse_args` is the call to extract). It makes the test trivial.

- [ ] **Step 2: Run, expect fail**

```bash
pytest tests/test_cli.py::test_cli_agent_flag_overrides_default -v
```

Expected: AttributeError on `args.agent`.

- [ ] **Step 3: Add the flag**

In `osint/cli.py`, find the `add_argument` block for the `scan` subcommand. Add (next to `--passes`):

```python
s.add_argument(
    "--agent",
    choices=["react_v1", "leadqueue_v2"],
    default="react_v1",
    help="Agent runner. react_v1 = ReAct loop with multi-pass deepen "
         "(default; behaves like before this flag existed). "
         "leadqueue_v2 = priority-queue investigation with verifier loop.",
)
```

In the same file, find where `ScanConfig(**kwargs)` is built and add:

```python
kwargs["agent_version"] = args.agent
```

- [ ] **Step 4: Run, expect green**

```bash
pytest tests/test_cli.py -v
```

- [ ] **Step 5: Run dispatcher tests, expect green**

```bash
pytest tests/test_dispatcher.py -v
```

Expected: BOTH tests pass now (the rejection test runs because `ScanConfig` accepts `agent_version`).

- [ ] **Step 6: Commit**

```bash
git add osint/cli.py tests/test_cli.py
git commit -m "feat(cli): add --agent flag (defaults to react_v1)"
```

---

## Phase C — `ScanState` extensions (v2-only fields)

### Task C1: Add `findings`, `leads_log`, `verifier_iterations` to `ScanState`

**Files:**
- Modify: `osint/state.py` (add fields)
- Modify: `tests/test_state.py` (assert defaults are empty)

- [ ] **Step 1: Write the test**

```python
# tests/test_state.py — add at the bottom
def test_scanstate_v2_fields_default_empty():
    """v2-only fields default to empty containers so v1 scans serialize
    unchanged shape (the fields are present but empty)."""
    from osint.state import ScanState
    from osint.types import ScanConfig
    s = ScanState(scan_id="x", subject="Jane", config=ScanConfig())
    assert s.findings == []
    assert s.leads_log == []
    assert s.verifier_iterations == 0
```

- [ ] **Step 2: Run, expect fail**

```bash
pytest tests/test_state.py::test_scanstate_v2_fields_default_empty -v
```

Expected: AttributeError on `findings`.

- [ ] **Step 3: Add fields**

In `osint/state.py`, find the `ScanState` dataclass and add (forward-reference the v2 types as strings to avoid circular imports):

```python
from dataclasses import dataclass, field
# ... existing imports

@dataclass
class ScanState:
    # ... existing fields
    # v2 lead-queue fields (unused by v1; serialize as empty defaults)
    findings: list = field(default_factory=list)        # list[Finding]
    leads_log: list = field(default_factory=list)       # list[Lead]
    verifier_iterations: int = 0
```

> **Note for the implementer:** keep `findings: list` and `leads_log: list` untyped at the dataclass level so this module doesn't have to import `osint.agents.leadqueue_v2.queue` (which would be a circular import in the v2 runner direction). Type-narrow at use site inside the v2 runner.

- [ ] **Step 4: Run, expect green**

```bash
pytest tests/test_state.py -v
```

- [ ] **Step 5: Verify v1 serialization is unchanged**

Look at any existing test that loads a serialized scan and check it still works:

```bash
pytest tests/test_storage.py -v
```

Expected: no regressions.

- [ ] **Step 6: Commit**

```bash
git add osint/state.py tests/test_state.py
git commit -m "feat(state): add findings/leads_log/verifier_iterations for v2"
```

---

## Phase D — Lead-Queue v2 components (TDD, bottom-up)

### Task D1: Lead, Source, Finding models + LeadQueue (queue.py)

**Files:**
- Create: `osint/agents/leadqueue_v2/__init__.py`
- Create: `osint/agents/leadqueue_v2/queue.py`
- Create: `tests/agents/leadqueue_v2/__init__.py`
- Create: `tests/agents/leadqueue_v2/test_queue.py`

- [ ] **Step 1: Create empty package init files**

```bash
mkdir -p osint/agents/leadqueue_v2
touch osint/agents/leadqueue_v2/__init__.py
mkdir -p tests/agents/leadqueue_v2
touch tests/agents/leadqueue_v2/__init__.py
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/agents/leadqueue_v2/test_queue.py
from datetime import datetime, timezone

import pytest

from osint.agents.leadqueue_v2.queue import (
    Finding,
    Lead,
    LeadQueue,
    Source,
)


def _lead(description: str, priority: int = 50, depth: int = 0) -> Lead:
    return Lead(
        id=f"l-{description[:8]}",
        kind="test",
        description=description,
        priority=priority,
        depth=depth,
        parent_lead_id=None,
        created_at=datetime.now(timezone.utc),
    )


def test_lead_dedup_hash_normalizes_whitespace_and_case():
    """Two leads with descriptions that differ only by whitespace/case
    must hash to the same dedup key — otherwise the LLM can spam the
    queue with trivial variations."""
    a = _lead("Investigate handle simonwen.eth")
    b = _lead("  investigate handle SimonWen.eth  ")
    assert a.dedup_key() == b.dedup_key()


def test_leadqueue_pop_returns_highest_priority_first():
    """Higher priority pops before lower priority. Tie-break by
    insertion order (FIFO within the same priority)."""
    q = LeadQueue()
    q.push(_lead("a", priority=10))
    q.push(_lead("b", priority=100))
    q.push(_lead("c", priority=50))
    assert q.pop().description == "b"
    assert q.pop().description == "c"
    assert q.pop().description == "a"
    assert q.pop() is None


def test_leadqueue_dedup_on_push():
    """Pushing a lead whose description already exists (popped or not)
    is a silent no-op — push() returns False; the queue size doesn't grow."""
    q = LeadQueue()
    assert q.push(_lead("investigate X")) is True
    assert q.push(_lead("Investigate X")) is False     # case-insensitive dedup
    assert q.push(_lead("  investigate x  ")) is False  # whitespace-insensitive
    # Pop the one we put in; pushing the same description AGAIN must still dedup.
    q.pop()
    assert q.push(_lead("investigate X")) is False, (
        "Once a lead has been seen, it stays seen — popping doesn't re-open the dedup slot. "
        "Otherwise a verifier proposing a previously-processed lead would re-run it."
    )


def test_leadqueue_empty_after_drain():
    q = LeadQueue()
    q.push(_lead("only one"))
    assert not q.empty()
    q.pop()
    assert q.empty()


def test_finding_requires_at_least_one_source():
    """Findings without evidence are rejected at construction time —
    a synthesizer must never produce uncited claims."""
    with pytest.raises(ValueError):
        Finding(
            id="f-1",
            claim="subject likes pizza",
            evidence=[],
            confidence="medium",
            lead_id="l-test",
            tags=[],
        )
```

- [ ] **Step 3: Run, expect fail**

```bash
pytest tests/agents/leadqueue_v2/test_queue.py -v
```

Expected: ImportError or ModuleNotFoundError on `osint.agents.leadqueue_v2.queue`.

- [ ] **Step 4: Implement `osint/agents/leadqueue_v2/queue.py`**

```python
# osint/agents/leadqueue_v2/queue.py
"""Priority queue of leads + the per-lead Lead/Source/Finding models.

The queue is in-memory only — its history is captured in
ScanState.leads_log as leads are popped, so an audit trail of the
investigation survives the scan even though the live queue does not.

Dedup happens by description hash (lower-cased, whitespace-stripped) so
the LLM can't spam the queue with trivial restatements of the same lead.
The seen-set is *append-only* — popping a lead does not re-open its slot.
That is intentional: if the verifier proposes a lead that was already
processed, it should be rejected, not re-run.
"""
from __future__ import annotations

import heapq
import itertools
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Source(BaseModel):
    tool_call_id: str
    snippet_quote: str   # the literal text from the tool result


class Finding(BaseModel):
    id: str
    claim: str
    evidence: list[Source] = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]
    lead_id: str
    tags: list[str] = Field(default_factory=list)


class Lead(BaseModel):
    id: str
    kind: str               # informal tag; logging + dedup only, NOT branching
    description: str
    priority: int           # higher = process first
    depth: int = 0          # 0 = root; deeper = generated from a prior lead
    parent_lead_id: str | None = None
    created_at: datetime

    def dedup_key(self) -> str:
        """Normalised hash key for dedup. Lower-cased, whitespace-collapsed."""
        return " ".join(self.description.lower().split())


class LeadQueue:
    """Priority queue with append-only seen-set for dedup.

    Internals: a binary heap of (-priority, insertion_counter, Lead). The
    counter breaks ties as FIFO and keeps the heap-ordered without
    needing Lead to be comparable.
    """

    def __init__(self) -> None:
        self._heap: list[tuple[int, int, Lead]] = []
        self._counter = itertools.count()
        self._seen: set[str] = set()

    def push(self, lead: Lead) -> bool:
        """Push a lead. Returns False if dedup'd (lead's key has been seen)."""
        key = lead.dedup_key()
        if key in self._seen:
            return False
        self._seen.add(key)
        heapq.heappush(self._heap, (-lead.priority, next(self._counter), lead))
        return True

    def pop(self) -> Lead | None:
        if not self._heap:
            return None
        return heapq.heappop(self._heap)[2]

    def is_seen(self, lead: Lead) -> bool:
        return lead.dedup_key() in self._seen

    def empty(self) -> bool:
        return not self._heap

    def __len__(self) -> int:
        return len(self._heap)
```

- [ ] **Step 5: Run, expect green**

```bash
pytest tests/agents/leadqueue_v2/test_queue.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add osint/agents/leadqueue_v2/__init__.py osint/agents/leadqueue_v2/queue.py tests/agents/leadqueue_v2/
git commit -m "feat(leadqueue_v2): Lead/Source/Finding models + priority queue with dedup"
```

---

### Task D2: Processor prompts

**Files:**
- Create: `osint/agents/leadqueue_v2/prompts.py`

- [ ] **Step 1: Implement prompt-builder helpers**

```python
# osint/agents/leadqueue_v2/prompts.py
"""Prompt templates for the lead-queue agent's three LLM personas:
processor (per-lead investigator), synthesizer (final report writer),
verifier (gap-finder + lead-proposer)."""
from __future__ import annotations

import json


PROCESSOR_SYSTEM = """\
You are processing ONE lead in an OSINT investigation. Your job is small
and focused — investigate ONLY this lead, then return findings + new
leads.

Inputs you'll see:
- Subject: the person being investigated
- Lead: a focused instruction (e.g. "investigate handle simonwen.eth")
- Findings so far: a compact bullet list of previously-confirmed facts
- Available tools: web_search, web_extract, apify_*, maigret (per scan
  config)

Output: a single JSON object, no prose:

```json
{
  "findings": [
    {
      "claim": "<natural-language fact>",
      "evidence": [
        {"tool_call_id": "<id>", "snippet_quote": "<exact text>"}
      ],
      "confidence": "high" | "medium" | "low",
      "tags": ["handle", "instagram"]
    }
  ],
  "new_leads": [
    {
      "kind": "investigate_url",
      "description": "<focused instruction for next investigator>",
      "priority": 1-100
    }
  ]
}
```

Rules:
- Run AT MOST 5 tool calls per lead. If a tool result is rich, prefer
  generating new_leads over making more tool calls yourself.
- Every claim MUST cite at least one tool call you ACTUALLY made in
  this turn. Do NOT cite findings from prior leads — those are already
  recorded.
- Do NOT include findings outside this lead's scope, but DO emit
  new_leads for things you noticed-but-didn't-investigate.
- Keep new_leads focused: one investigation per lead, not "do everything
  you can find". Ten focused leads beat one mega-lead.
"""


SYNTHESIZER_SYSTEM = """\
You are writing the final OSINT report from a complete findings record.
Every claim in the report MUST be grounded in a Finding from the input.

Findings format: a list of {claim, evidence: [{tool_call_id, snippet_quote}],
confidence, tags}.

Output the same prose-plus-tail-JSON format the previous OSINT system
used:

  1. Full prose report with these sections:
       **Executive Summary**
       **Identified Name Variations & Aliases**
       **Comprehensive Profile** (Personal Background, Education,
                                  Professional History, Geographic
                                  Footprint, etc.)
       **Digital & Social Media Footprint**
       **Key Associates & Network Map**
       **Timeline of Significant Events**
       **Hypotheses, Patterns & Potential Red Flags** (with confidence)
       **Leads for Further Investigation**
       **Sources** — for EVERY major claim, cite the tool call inline
                    (use the tool_call_id from the evidence).
       **Overall Assessment**

  2. Then ONE fenced JSON block at the very end:

```json
{{
  "extracted_identifiers": {{
    "emails": [...], "usernames": [...], "urls": [...],
    "name_variations": [...], "schools": [...], "employers": [...],
    "phones": [...], "addresses": [...]
  }}
}}
```

Rules:
- If a finding has confidence=low, mark it explicitly in the prose
  (e.g. "(low confidence)").
- Do NOT make up sources. Every cited tool_call_id must come from the
  findings list.
- Group findings by tag where the report sections suggest it (e.g.
  handle-tagged findings → Digital & Social Media Footprint).
"""


VERIFIER_SYSTEM = """\
You are auditing an OSINT report for coverage and grounding. You read
the draft report, the full findings list, and the list of leads
already processed. Return one of:

  - {"satisfied": true, "gaps": [], "new_leads": []} — accept the report
  - {"satisfied": false, "gaps": [...], "new_leads": [...]} — request
    more investigation

When to mark UN-satisfied:
- A claim in the report has no matching finding (ungrounded). List it
  as a gap; ALSO emit a new_lead with description = "verify the claim
  '<text>' or remove it from the report".
- An obvious dimension is missing. Examples: report mentions employer
  but no LinkedIn evidence; subject is technical but no GitHub probe
  done; subject is Chinese but no zhihu/weibo searches; report mentions
  a project name without sources. List as gap; emit a new_lead.

When to mark satisfied:
- Every report claim is grounded.
- The investigation has addressed each Mandatory Search Dimension that
  has plausible signal for this subject.
- You've already proposed leads on this dimension in a prior verifier
  iteration AND they were processed (check the leads_log).

Output one JSON object, no prose. Keep new_leads:
- focused (one investigation per lead)
- different from leads in leads_log (the queue dedups but the LLM
  shouldn't waste a slot on a duplicate)
- prioritized 80–100 (verifier-proposed leads should jump the queue)
"""


def format_findings_compact(findings: list, max_chars: int = 6000) -> str:
    """One-line-per-finding summary fed to processor + verifier prompts.

    Truncated at max_chars so the running findings record can't blow
    the LLM's context window.
    """
    lines = []
    for i, f in enumerate(findings):
        # f is a Finding model; render claim + first-evidence snippet
        ev = f.evidence[0] if f.evidence else None
        evstr = f"[{ev.tool_call_id}] {ev.snippet_quote[:80]}" if ev else "(no evidence)"
        line = f"{i+1}. ({f.confidence}) {f.claim}  ← {evstr}"
        lines.append(line)
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n…(truncated)"
    return out


def format_leads_log_compact(leads_log: list, max_chars: int = 2000) -> str:
    """One-line-per-lead summary of already-processed leads, fed to verifier."""
    lines = [f"{i+1}. ({lead.kind}, p={lead.priority}) {lead.description}"
             for i, lead in enumerate(leads_log)]
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n…(truncated)"
    return out
```

- [ ] **Step 2: No tests at this point** — the prompts module is pure data; it'll be exercised end-to-end via processor / synthesizer / verifier tests.

- [ ] **Step 3: Commit**

```bash
git add osint/agents/leadqueue_v2/prompts.py
git commit -m "feat(leadqueue_v2): add processor/synthesizer/verifier prompt templates"
```

---

### Task D3: Processor — `process_one_lead`

**Files:**
- Create: `osint/agents/leadqueue_v2/processor.py`
- Create: `tests/agents/leadqueue_v2/test_processor.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/leadqueue_v2/test_processor.py
"""Processor: takes one Lead + the running findings record, runs a
small ReAct mini-loop, returns structured (findings, new_leads)."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from osint.agents.leadqueue_v2.processor import process_one_lead
from osint.agents.leadqueue_v2.queue import Finding, Lead


class BindableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self


def _lead(description: str = "investigate X", priority: int = 50) -> Lead:
    return Lead(
        id="l-test",
        kind="test_kind",
        description=description,
        priority=priority,
        depth=0,
        parent_lead_id=None,
        created_at=datetime.now(timezone.utc),
    )


def _ai_with_json(payload: dict) -> AIMessage:
    """Wrap a dict in the prose-then-fenced-JSON form processor expects."""
    import json
    return AIMessage(
        content=f"Processed.\n\n```json\n{json.dumps(payload)}\n```\n",
        tool_calls=[],
    )


async def test_process_one_lead_parses_findings_and_new_leads():
    """Happy path: LLM emits structured JSON; processor returns
    typed (findings, new_leads)."""
    payload = {
        "findings": [
            {
                "claim": "subject's IG handle is simonwen.eth",
                "evidence": [
                    {"tool_call_id": "tc-1", "snippet_quote": "instagram.com/simonwen.eth"}
                ],
                "confidence": "high",
                "tags": ["handle", "instagram"],
            }
        ],
        "new_leads": [
            {
                "kind": "investigate_handle",
                "description": "fetch simonwen.eth IG profile",
                "priority": 70,
            }
        ],
    }
    fake = BindableFake(responses=[_ai_with_json(payload)])
    findings, new_leads = await process_one_lead(
        subject="Jane",
        lead=_lead(),
        all_findings=[],
        llm=fake,
        tools=[],
        cost_cb=MagicMock(),
    )
    assert len(findings) == 1
    assert findings[0].claim == "subject's IG handle is simonwen.eth"
    assert findings[0].lead_id == "l-test"
    assert findings[0].evidence[0].tool_call_id == "tc-1"
    assert len(new_leads) == 1
    assert new_leads[0].description == "fetch simonwen.eth IG profile"
    assert new_leads[0].priority == 70
    assert new_leads[0].depth == 1, "lead depth must increment from parent's depth"
    assert new_leads[0].parent_lead_id == "l-test"


async def test_process_one_lead_handles_malformed_json_with_retry():
    """If the LLM returns malformed JSON, processor retries once.
    On second failure, it returns empty findings + empty new_leads
    (lead is consumed, not requeued — sticky-error guard)."""
    fake = BindableFake(responses=[
        AIMessage(content="not json at all", tool_calls=[]),
        AIMessage(content="still not json", tool_calls=[]),
    ])
    findings, new_leads = await process_one_lead(
        subject="Jane",
        lead=_lead(),
        all_findings=[],
        llm=fake,
        tools=[],
        cost_cb=MagicMock(),
    )
    assert findings == []
    assert new_leads == []


async def test_process_one_lead_recovers_on_retry():
    """LLM returns malformed JSON first, valid JSON on retry → success."""
    payload = {
        "findings": [
            {
                "claim": "subject went to NYU",
                "evidence": [{"tool_call_id": "tc-1", "snippet_quote": "..."}],
                "confidence": "medium",
                "tags": ["education"],
            }
        ],
        "new_leads": [],
    }
    fake = BindableFake(responses=[
        AIMessage(content="garbage", tool_calls=[]),
        _ai_with_json(payload),
    ])
    findings, new_leads = await process_one_lead(
        subject="Jane",
        lead=_lead(),
        all_findings=[],
        llm=fake,
        tools=[],
        cost_cb=MagicMock(),
    )
    assert len(findings) == 1
    assert findings[0].confidence == "medium"
```

- [ ] **Step 2: Run, expect fail**

```bash
pytest tests/agents/leadqueue_v2/test_processor.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `processor.py`**

```python
# osint/agents/leadqueue_v2/processor.py
"""Processor: runs one Lead through a small ReAct mini-loop and parses
the LLM's structured output into (findings, new_leads).

The mini-loop is bounded by `max_processor_tool_calls` (default 5) so
a single lead can't burn the whole scan budget.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from osint.agents.leadqueue_v2.prompts import (
    PROCESSOR_SYSTEM,
    format_findings_compact,
)
from osint.agents.leadqueue_v2.queue import Finding, Lead, Source

_FENCED_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_processor_output(text: str, lead: Lead) -> tuple[list[Finding], list[Lead]]:
    """Parse the LLM's terminal message into (findings, new_leads).

    Raises ValueError if the JSON envelope is missing or malformed —
    caller decides whether to retry."""
    m = _FENCED_JSON.search(text)
    if not m:
        # Fall back: try a bare JSON object at the end of the message.
        stripped = text.strip()
        if not (stripped.startswith("{") and stripped.endswith("}")):
            raise ValueError("processor output missing fenced JSON envelope")
        body = stripped
    else:
        body = m.group(1)
    data = json.loads(body)

    findings: list[Finding] = []
    for f in data.get("findings", []):
        findings.append(Finding(
            id=f.get("id") or f"f-{uuid.uuid4().hex[:8]}",
            claim=f["claim"],
            evidence=[Source(**e) for e in f["evidence"]],
            confidence=f["confidence"],
            lead_id=lead.id,
            tags=f.get("tags", []),
        ))

    new_leads: list[Lead] = []
    now = datetime.now(timezone.utc)
    for nl in data.get("new_leads", []):
        new_leads.append(Lead(
            id=f"l-{uuid.uuid4().hex[:8]}",
            kind=nl["kind"],
            description=nl["description"],
            priority=int(nl["priority"]),
            depth=lead.depth + 1,
            parent_lead_id=lead.id,
            created_at=now,
        ))
    return findings, new_leads


async def process_one_lead(
    *,
    subject: str,
    lead: Lead,
    all_findings: list[Finding],
    llm: BaseChatModel,
    tools: list[Any],
    cost_cb: Any,
    max_processor_tool_calls: int = 5,
) -> tuple[list[Finding], list[Lead]]:
    """Process one Lead. Returns (findings, new_leads)."""
    findings_summary = format_findings_compact(all_findings)
    user_msg = (
        f"SUBJECT:\n{subject}\n\n"
        f"LEAD ({lead.kind}, priority={lead.priority}, depth={lead.depth}):\n"
        f"{lead.description}\n\n"
        f"FINDINGS SO FAR:\n{findings_summary or '(none)'}\n\n"
        f"Investigate this lead. Use AT MOST {max_processor_tool_calls} tool calls. "
        f"Return findings + new_leads as a single JSON envelope per the system prompt."
    )

    messages = [
        SystemMessage(content=PROCESSOR_SYSTEM),
        HumanMessage(content=user_msg),
    ]

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            agent = create_react_agent(model=llm, tools=tools, prompt=None)
            result = await agent.ainvoke(
                {"messages": messages},
                config={
                    "callbacks": [cost_cb],
                    "recursion_limit": max_processor_tool_calls * 2 + 5,
                },
            )
            # Last AI message holds the structured output
            last_ai = next(
                (m for m in reversed(result.get("messages", []))
                 if m.__class__.__name__ == "AIMessage"),
                None,
            )
            text = (getattr(last_ai, "content", "") or "") if last_ai else ""
            return _parse_processor_output(text, lead)
        except Exception as e:  # parsing failed OR LangGraph errored
            last_error = e
            continue

    # Both attempts failed — consume the lead silently.
    return [], []
```

- [ ] **Step 4: Run, expect green**

```bash
pytest tests/agents/leadqueue_v2/test_processor.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add osint/agents/leadqueue_v2/processor.py tests/agents/leadqueue_v2/test_processor.py
git commit -m "feat(leadqueue_v2): processor — runs one lead, parses findings + new leads"
```

---

### Task D4: Synthesizer

**Files:**
- Create: `osint/agents/leadqueue_v2/synthesizer.py`
- Create: `tests/agents/leadqueue_v2/test_synthesizer.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/leadqueue_v2/test_synthesizer.py
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from osint.agents.leadqueue_v2.queue import Finding, Source
from osint.agents.leadqueue_v2.synthesizer import synthesize


class BindableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self


def _f(claim: str, conf: str = "high", tags: list[str] | None = None) -> Finding:
    return Finding(
        id="f-1",
        claim=claim,
        evidence=[Source(tool_call_id="tc-1", snippet_quote="evidence text")],
        confidence=conf,
        lead_id="l-1",
        tags=tags or [],
    )


REPORT_TEXT = """**Executive Summary**

Jane is a SWE in NYC.

**Sources**
- tc-1: ...

```json
{"extracted_identifiers": {"emails": ["jane@example.com"]}}
```
"""


async def test_synthesize_passes_findings_to_llm_and_returns_parsed_report():
    fake = BindableFake(responses=[AIMessage(content=REPORT_TEXT, tool_calls=[])])
    parsed = await synthesize(
        subject="Jane",
        findings=[_f("Jane is a SWE", tags=["career"])],
        llm=fake,
        cost_cb=MagicMock(),
    )
    assert "Executive Summary" in parsed["report"]["text"]
    assert parsed["extracted_identifiers"] == {"emails": ["jane@example.com"]}


async def test_synthesize_handles_empty_findings():
    """If there are no findings, synthesizer still returns a parseable
    report — don't crash on a sparse subject."""
    fake = BindableFake(responses=[
        AIMessage(content="Nothing found.\n\n```json\n{\"extracted_identifiers\": {}}\n```",
                  tool_calls=[])
    ])
    parsed = await synthesize(subject="Jane", findings=[], llm=fake, cost_cb=MagicMock())
    assert parsed["extracted_identifiers"] == {}
    assert "Nothing found" in parsed["report"]["text"]


async def test_synthesize_falls_back_when_llm_returns_empty_content():
    """Grok-4.20's reasoning-mode 0-token bug — second call must still
    produce SOMETHING. Synthesizer retries once, then returns whatever
    text it has (even empty) wrapped in the standard parsed shape."""
    fake = BindableFake(responses=[
        AIMessage(content="", tool_calls=[]),     # first attempt: empty
        AIMessage(content=REPORT_TEXT, tool_calls=[]),  # retry: real
    ])
    parsed = await synthesize(subject="Jane", findings=[_f("X")], llm=fake, cost_cb=MagicMock())
    assert "Executive Summary" in parsed["report"]["text"]
```

- [ ] **Step 2: Run, expect fail**

```bash
pytest tests/agents/leadqueue_v2/test_synthesizer.py -v
```

- [ ] **Step 3: Implement `synthesizer.py`**

```python
# osint/agents/leadqueue_v2/synthesizer.py
"""Synthesizer: one LLM call to merge all findings into the final report."""
from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from osint.agents.leadqueue_v2.prompts import (
    SYNTHESIZER_SYSTEM,
    format_findings_compact,
)
from osint.agents.leadqueue_v2.queue import Finding
from osint.agents.react_v1.prompts import parse_report  # reuse v1's parser


async def synthesize(
    *,
    subject: str,
    findings: list[Finding],
    llm: BaseChatModel,
    cost_cb: Any,
) -> dict:
    """Returns a parsed-report dict matching parse_report()'s schema:
    {"extracted_identifiers": {...}, "report": {"text": "..."}}."""
    findings_block = (
        format_findings_compact(findings, max_chars=20_000)
        if findings else "(no findings)"
    )
    user_msg = (
        f"SUBJECT:\n{subject}\n\nFINDINGS:\n{findings_block}\n\n"
        f"Produce the final report per the system prompt's format."
    )
    msgs = [
        SystemMessage(content=SYNTHESIZER_SYSTEM),
        HumanMessage(content=user_msg),
    ]
    # First attempt
    result = await llm.ainvoke(msgs, config={"callbacks": [cost_cb]})
    text = result.content or ""
    if not text.strip():
        # Grok-4.20 reasoning-mode 0-token bug: retry once.
        result = await llm.ainvoke(msgs, config={"callbacks": [cost_cb]})
        text = result.content or ""
    return parse_report(text)
```

- [ ] **Step 4: Run, expect green**

```bash
pytest tests/agents/leadqueue_v2/test_synthesizer.py -v
```

- [ ] **Step 5: Commit**

```bash
git add osint/agents/leadqueue_v2/synthesizer.py tests/agents/leadqueue_v2/test_synthesizer.py
git commit -m "feat(leadqueue_v2): synthesizer — findings → final report"
```

---

### Task D5: Verifier

**Files:**
- Create: `osint/agents/leadqueue_v2/verifier.py`
- Create: `tests/agents/leadqueue_v2/test_verifier.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/leadqueue_v2/test_verifier.py
from datetime import datetime, timezone
from unittest.mock import MagicMock

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from osint.agents.leadqueue_v2.queue import Finding, Lead, Source
from osint.agents.leadqueue_v2.verifier import VerifierResult, verify


class BindableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self


def _f(claim: str) -> Finding:
    return Finding(
        id="f-1",
        claim=claim,
        evidence=[Source(tool_call_id="tc-1", snippet_quote="...")],
        confidence="high",
        lead_id="l-1",
        tags=[],
    )


def _l(description: str) -> Lead:
    return Lead(
        id="l-1",
        kind="test",
        description=description,
        priority=50,
        depth=0,
        parent_lead_id=None,
        created_at=datetime.now(timezone.utc),
    )


def _ai(json_body: str) -> AIMessage:
    return AIMessage(content=f"```json\n{json_body}\n```", tool_calls=[])


async def test_verifier_satisfied_returns_no_new_leads():
    fake = BindableFake(responses=[
        _ai('{"satisfied": true, "gaps": [], "new_leads": []}')
    ])
    result = await verify(
        subject="Jane",
        report_text="ok",
        findings=[_f("X")],
        leads_log=[_l("a")],
        llm=fake,
        cost_cb=MagicMock(),
    )
    assert isinstance(result, VerifierResult)
    assert result.satisfied is True
    assert result.new_leads == []


async def test_verifier_unsatisfied_returns_new_leads():
    body = (
        '{"satisfied": false,'
        ' "gaps": ["no GitHub probe done"],'
        ' "new_leads": [{"kind":"github","description":"search github for jane","priority":90}]}'
    )
    fake = BindableFake(responses=[_ai(body)])
    result = await verify(
        subject="Jane",
        report_text="...",
        findings=[],
        leads_log=[],
        llm=fake,
        cost_cb=MagicMock(),
    )
    assert result.satisfied is False
    assert len(result.new_leads) == 1
    assert result.new_leads[0].kind == "github"
    assert result.new_leads[0].priority == 90


async def test_verifier_malformed_json_treated_as_satisfied_on_retry_failure():
    """Per spec: if verifier returns malformed JSON twice, accept the
    draft (better to ship a slightly weaker report than burn budget)."""
    fake = BindableFake(responses=[
        AIMessage(content="totally not json", tool_calls=[]),
        AIMessage(content="still not json", tool_calls=[]),
    ])
    result = await verify(
        subject="Jane",
        report_text="...",
        findings=[],
        leads_log=[],
        llm=fake,
        cost_cb=MagicMock(),
    )
    assert result.satisfied is True
    assert result.new_leads == []
```

- [ ] **Step 2: Run, expect fail**

```bash
pytest tests/agents/leadqueue_v2/test_verifier.py -v
```

- [ ] **Step 3: Implement `verifier.py`**

```python
# osint/agents/leadqueue_v2/verifier.py
"""Verifier: scores the draft report's coverage + grounding, returns
either {satisfied=True} or a list of new leads to push onto the queue."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from osint.agents.leadqueue_v2.prompts import (
    VERIFIER_SYSTEM,
    format_findings_compact,
    format_leads_log_compact,
)
from osint.agents.leadqueue_v2.queue import Finding, Lead


_FENCED_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


class VerifierResult(BaseModel):
    satisfied: bool
    gaps: list[str]
    new_leads: list[Lead]


def _parse_verifier_output(text: str) -> VerifierResult:
    m = _FENCED_JSON.search(text)
    if m:
        body = m.group(1)
    else:
        stripped = text.strip()
        if not (stripped.startswith("{") and stripped.endswith("}")):
            raise ValueError("verifier output missing JSON envelope")
        body = stripped
    data = json.loads(body)
    new_leads: list[Lead] = []
    now = datetime.now(timezone.utc)
    for nl in data.get("new_leads", []):
        new_leads.append(Lead(
            id=f"l-{uuid.uuid4().hex[:8]}",
            kind=nl["kind"],
            description=nl["description"],
            priority=int(nl.get("priority", 80)),
            depth=0,                  # verifier-leads are top-level
            parent_lead_id=None,
            created_at=now,
        ))
    return VerifierResult(
        satisfied=bool(data.get("satisfied", False)),
        gaps=list(data.get("gaps", [])),
        new_leads=new_leads,
    )


async def verify(
    *,
    subject: str,
    report_text: str,
    findings: list[Finding],
    leads_log: list[Lead],
    llm: BaseChatModel,
    cost_cb: Any,
) -> VerifierResult:
    user_msg = (
        f"SUBJECT:\n{subject}\n\n"
        f"DRAFT REPORT:\n{report_text}\n\n"
        f"FINDINGS:\n{format_findings_compact(findings)}\n\n"
        f"LEADS ALREADY PROCESSED:\n{format_leads_log_compact(leads_log)}\n\n"
        f"Score the report and return your JSON envelope."
    )
    msgs = [
        SystemMessage(content=VERIFIER_SYSTEM),
        HumanMessage(content=user_msg),
    ]
    for _attempt in (1, 2):
        try:
            r = await llm.ainvoke(msgs, config={"callbacks": [cost_cb]})
            return _parse_verifier_output(r.content or "")
        except Exception:
            continue
    # Both attempts failed → accept the draft.
    return VerifierResult(satisfied=True, gaps=[], new_leads=[])
```

- [ ] **Step 4: Run, expect green**

```bash
pytest tests/agents/leadqueue_v2/test_verifier.py -v
```

- [ ] **Step 5: Commit**

```bash
git add osint/agents/leadqueue_v2/verifier.py tests/agents/leadqueue_v2/test_verifier.py
git commit -m "feat(leadqueue_v2): verifier — scores report coverage + emits new leads"
```

---

### Task D6: Runner glue (`LeadQueueV2Runner`)

**Files:**
- Create: `osint/agents/leadqueue_v2/runner.py`
- Modify: `osint/agents/leadqueue_v2/__init__.py` (export the runner)
- Create: `tests/agents/leadqueue_v2/test_runner.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/leadqueue_v2/test_runner.py
"""End-to-end LeadQueueV2Runner test using BindableFake + mock tools.

The runner threads through 5 phases (seed, main loop, synthesize,
verifier loop, final). Each test below pins one of those phases'
contracts."""
import json
from unittest.mock import MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from osint.agents.leadqueue_v2.runner import LeadQueueV2Runner
from osint.state import ScanState, StopReason
from osint.types import ScanConfig


class BindableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self


def _ai(payload: dict) -> AIMessage:
    return AIMessage(
        content=f"```json\n{json.dumps(payload)}\n```",
        tool_calls=[],
    )


# Canned LLM responses for a 1-lead 1-finding 1-iteration happy path.
PROCESSOR_OUTPUT = {
    "findings": [{
        "claim": "subject went to NYU",
        "evidence": [{"tool_call_id": "tc-1", "snippet_quote": "..."}],
        "confidence": "high",
        "tags": ["education"],
    }],
    "new_leads": [],
}
SYNTH_OUTPUT_PROSE_PLUS_JSON = (
    "**Executive Summary**\n\nJane went to NYU.\n\n"
    "```json\n{\"extracted_identifiers\": {\"schools\": [\"NYU\"]}}\n```"
)
VERIFIER_SATISFIED = {"satisfied": True, "gaps": [], "new_leads": []}


async def test_runner_happy_path_emits_report_with_findings():
    """Identity-lock lead → 1 finding → no new leads → synth → verifier
    satisfied → done."""
    fake = BindableFake(responses=[
        _ai(PROCESSOR_OUTPUT),                               # identity-lock processor
        AIMessage(content=SYNTH_OUTPUT_PROSE_PLUS_JSON, tool_calls=[]),  # synthesizer
        _ai(VERIFIER_SATISFIED),                             # verifier
    ])
    state = ScanState(scan_id="x", subject="Jane", config=ScanConfig(agent_version="leadqueue_v2"))
    runner = LeadQueueV2Runner()
    parsed, stop_reason = await runner.run(
        subject="Jane",
        state=state,
        llm=fake,
        tools=[],
        cost_cb=MagicMock(),
    )
    assert stop_reason is None
    assert parsed["extracted_identifiers"] == {"schools": ["NYU"]}
    assert "Jane went to NYU" in parsed["report"]["text"]
    assert len(state.findings) == 1
    assert len(state.leads_log) == 1   # identity-lock lead
    assert state.verifier_iterations == 0   # finished without revision


async def test_runner_verifier_loop_pushes_new_leads_and_re_synthesizes():
    """Verifier returns unsatisfied once → runner processes new lead → re-synth → satisfied."""
    PROCESSOR_OUTPUT_2 = {
        "findings": [{
            "claim": "subject also has GitHub",
            "evidence": [{"tool_call_id": "tc-2", "snippet_quote": "..."}],
            "confidence": "high",
            "tags": ["handle"],
        }],
        "new_leads": [],
    }
    VERIFIER_UNSATISFIED = {
        "satisfied": False,
        "gaps": ["no GitHub probe"],
        "new_leads": [{"kind": "github", "description": "find subject's GitHub", "priority": 90}],
    }
    SYNTH_2 = (
        "**Executive Summary**\n\nJane went to NYU and has a GitHub.\n\n"
        "```json\n{\"extracted_identifiers\": {\"schools\": [\"NYU\"], \"usernames\": [\"jane\"]}}\n```"
    )
    fake = BindableFake(responses=[
        _ai(PROCESSOR_OUTPUT),       # phase 1 lead
        AIMessage(content=SYNTH_OUTPUT_PROSE_PLUS_JSON, tool_calls=[]),
        _ai(VERIFIER_UNSATISFIED),   # iteration 1: not satisfied
        _ai(PROCESSOR_OUTPUT_2),     # process the verifier's new lead
        AIMessage(content=SYNTH_2, tool_calls=[]),
        _ai(VERIFIER_SATISFIED),     # iteration 2: satisfied
    ])
    state = ScanState(scan_id="x", subject="Jane", config=ScanConfig(agent_version="leadqueue_v2"))
    runner = LeadQueueV2Runner()
    parsed, _ = await runner.run(
        subject="Jane", state=state, llm=fake, tools=[], cost_cb=MagicMock(),
    )
    assert "GitHub" in parsed["report"]["text"]
    assert state.verifier_iterations == 1
    assert len(state.findings) == 2
    assert len(state.leads_log) == 2   # identity-lock + verifier-pushed lead


async def test_runner_verifier_loop_caps_at_max_iterations():
    """If verifier never returns satisfied=True, runner stops after
    config.max_verifier_iterations and returns the latest draft."""
    UNSAT_NEW_LEAD = {
        "satisfied": False,
        "gaps": ["X"],
        "new_leads": [{"kind": "k", "description": "d", "priority": 90}],
    }
    fake = BindableFake(responses=[
        _ai(PROCESSOR_OUTPUT),                                     # phase 1 lead
        AIMessage(content=SYNTH_OUTPUT_PROSE_PLUS_JSON, tool_calls=[]),
        # 3 verifier iterations, each unsatisfied — runner caps here.
        _ai(UNSAT_NEW_LEAD), _ai(PROCESSOR_OUTPUT), AIMessage(content=SYNTH_OUTPUT_PROSE_PLUS_JSON, tool_calls=[]),
        _ai(UNSAT_NEW_LEAD), _ai(PROCESSOR_OUTPUT), AIMessage(content=SYNTH_OUTPUT_PROSE_PLUS_JSON, tool_calls=[]),
        _ai(UNSAT_NEW_LEAD), _ai(PROCESSOR_OUTPUT), AIMessage(content=SYNTH_OUTPUT_PROSE_PLUS_JSON, tool_calls=[]),
    ])
    state = ScanState(
        scan_id="x", subject="Jane",
        config=ScanConfig(agent_version="leadqueue_v2", max_verifier_iterations=3),
    )
    runner = LeadQueueV2Runner()
    await runner.run(subject="Jane", state=state, llm=fake, tools=[], cost_cb=MagicMock())
    assert state.verifier_iterations == 3, "must cap at max_verifier_iterations"
```

- [ ] **Step 2: Run, expect fail**

```bash
pytest tests/agents/leadqueue_v2/test_runner.py -v
```

- [ ] **Step 3: Implement `runner.py`**

```python
# osint/agents/leadqueue_v2/runner.py
"""LeadQueueV2Runner — entrypoint for the lead-queue agent.

Phases (per the spec):
  1. Seed: push identity-lock lead onto queue
  2. Main loop: pop → process → record → push new leads, until empty or stop
  3. Synthesize: findings → draft report
  4. Verifier loop: ≤ max_verifier_iterations
       - if satisfied: break
       - else: push verifier's new_leads; drain main loop; re-synth
  5. Return parsed report
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models import BaseChatModel

from osint.agents.leadqueue_v2.processor import process_one_lead
from osint.agents.leadqueue_v2.queue import Lead, LeadQueue
from osint.agents.leadqueue_v2.synthesizer import synthesize
from osint.agents.leadqueue_v2.verifier import verify
from osint.log import logger
from osint.state import ScanState, StopReason


def _identity_lock_lead(subject: str) -> Lead:
    return Lead(
        id=f"l-{uuid.uuid4().hex[:8]}",
        kind="identity_lock",
        description=(
            f"Verify the identity of '{subject}'. Find ≥3 cross-reference "
            f"points (school + year, city, employer, distinct identifier) "
            f"that all match. Output identity-lock fact + initial leads "
            f"(handles to probe, URLs to extract, organizations to investigate)."
        ),
        priority=100,
        depth=0,
        parent_lead_id=None,
        created_at=datetime.now(timezone.utc),
    )


async def _drain_queue(
    *,
    queue: LeadQueue,
    subject: str,
    state: ScanState,
    llm: BaseChatModel,
    tools: list[Any],
    cost_cb: Any,
) -> None:
    """Pop and process leads until queue is empty or scan should stop.

    Mutates state in place: extends state.findings, appends to
    state.leads_log on each completed lead. New leads from the processor
    go back onto `queue` (subject to dedup)."""
    while not queue.empty():
        should_stop, _reason = state.should_stop()
        if should_stop:
            break
        lead = queue.pop()
        if lead is None:
            break
        findings, new_leads = await process_one_lead(
            subject=subject,
            lead=lead,
            all_findings=state.findings,
            llm=llm,
            tools=tools,
            cost_cb=cost_cb,
        )
        state.findings.extend(findings)
        state.leads_log.append(lead)
        for nl in new_leads:
            queue.push(nl)


class LeadQueueV2Runner:
    async def run(
        self,
        *,
        subject: str,
        state: ScanState,
        llm: BaseChatModel,
        tools: list[Any],
        cost_cb: Any,
    ) -> tuple[dict, StopReason | None]:
        config = state.config
        queue = LeadQueue()

        # Phase 1: seed
        queue.push(_identity_lock_lead(subject))

        # Phase 2: main loop
        await _drain_queue(
            queue=queue, subject=subject, state=state,
            llm=llm, tools=tools, cost_cb=cost_cb,
        )

        # Stop reason check before we spend on synth+verify
        should_stop, stop_reason = state.should_stop()

        # Phase 3: synthesize
        parsed = await synthesize(
            subject=subject, findings=state.findings,
            llm=llm, cost_cb=cost_cb,
        )

        # Phase 4: verifier loop (skipped if cap-cut already)
        if not should_stop:
            while state.verifier_iterations < config.max_verifier_iterations:
                vresult = await verify(
                    subject=subject,
                    report_text=parsed.get("report", {}).get("text") or "",
                    findings=state.findings,
                    leads_log=state.leads_log,
                    llm=llm, cost_cb=cost_cb,
                )
                if vresult.satisfied:
                    break
                # Push new leads, drain, re-synthesize.
                for nl in vresult.new_leads:
                    queue.push(nl)
                await _drain_queue(
                    queue=queue, subject=subject, state=state,
                    llm=llm, tools=tools, cost_cb=cost_cb,
                )
                parsed = await synthesize(
                    subject=subject, findings=state.findings,
                    llm=llm, cost_cb=cost_cb,
                )
                state.verifier_iterations += 1
                # Re-check stop conditions; verifier loop respects budget too.
                should_stop, stop_reason = state.should_stop()
                if should_stop:
                    break

        return parsed, stop_reason if should_stop else None
```

- [ ] **Step 4: Wire registry**

```python
# osint/agents/leadqueue_v2/__init__.py
from osint.agents.leadqueue_v2.runner import LeadQueueV2Runner

__all__ = ["LeadQueueV2Runner"]
```

```python
# osint/agents/__init__.py — update
from osint.agents.leadqueue_v2 import LeadQueueV2Runner
from osint.agents.react_v1 import ReactV1Runner

AGENTS = {
    "react_v1": ReactV1Runner,
    "leadqueue_v2": LeadQueueV2Runner,
}
```

- [ ] **Step 5: Run runner tests, expect green**

```bash
pytest tests/agents/leadqueue_v2/test_runner.py -v
```

- [ ] **Step 6: Run full suite, expect green**

```bash
pytest tests/ -q
```

Expected: existing tests still pass; new lead-queue tests added.

- [ ] **Step 7: Commit**

```bash
git add osint/agents/leadqueue_v2/runner.py osint/agents/leadqueue_v2/__init__.py osint/agents/__init__.py tests/agents/leadqueue_v2/test_runner.py
git commit -m "feat(leadqueue_v2): runner — phase loop + verifier loop + registry wiring"
```

---

### Task D7: Persist `findings` and `leads_log` in scan JSON

**Files:**
- Modify: `osint/types.py` (add `findings`, `leads_log` to `ScanResult`)
- Modify: `osint/state.py` (`to_result` writes them through)
- Modify: `osint/storage.py` (write_scan_json + write_scan_markdown handle them)

- [ ] **Step 1: Write the failing test**

In `tests/agents/leadqueue_v2/test_runner.py`, add at the bottom:

```python
async def test_runner_persists_findings_and_leads_log_through_scan_json(tmp_path):
    """The dispatcher writes scan JSON; v2 fields must round-trip."""
    import json as json_module
    from osint.run import scan
    fake = BindableFake(responses=[
        _ai(PROCESSOR_OUTPUT),
        AIMessage(content=SYNTH_OUTPUT_PROSE_PLUS_JSON, tool_calls=[]),
        _ai(VERIFIER_SATISFIED),
    ])
    cfg = ScanConfig(
        agent_version="leadqueue_v2",
        enabled_tools=set(),  # no tool-build env required
    )
    # need APIFY_TOKEN unset-safe path: enabled_tools is empty so tool factory
    # doesn't validate APIFY_TOKEN.
    result = await scan(subject="Jane", config=cfg, llm=fake, scans_dir=tmp_path)
    data = json_module.loads(result.path.read_text())
    assert "findings" in data and len(data["findings"]) == 1
    assert "leads_log" in data and len(data["leads_log"]) == 1
    assert data["findings"][0]["claim"] == "subject went to NYU"
```

- [ ] **Step 2: Run, expect fail**

```bash
pytest tests/agents/leadqueue_v2/test_runner.py::test_runner_persists_findings_and_leads_log_through_scan_json -v
```

- [ ] **Step 3: Add fields to `ScanResult`**

In `osint/types.py`:

```python
class ScanResult(BaseModel):
    # ... existing fields
    findings: list[dict] = Field(default_factory=list)   # serialized list[Finding]
    leads_log: list[dict] = Field(default_factory=list)  # serialized list[Lead]
```

- [ ] **Step 4: Update `state.to_result`**

In `osint/state.py`, find `ScanState.to_result(...)` and include the new fields:

```python
def to_result(self, scans_dir: Path) -> ScanResult:
    return ScanResult(
        # ... existing args
        findings=[f.model_dump(mode="json") for f in self.findings],
        leads_log=[l.model_dump(mode="json") for l in self.leads_log],
    )
```

> **Note:** if `findings` / `leads_log` ever contain a non-Pydantic-model entry, `model_dump` fails. Wrap the list comprehension in a guard that handles both:
> ```python
> findings=[f.model_dump(mode="json") if hasattr(f, "model_dump") else f for f in self.findings]
> ```

- [ ] **Step 5: Update `write_scan_markdown` to render lead summary**

In `osint/storage.py`, find `write_scan_markdown` and add a section after the existing "Tool Call Log" block:

```python
if result.leads_log:
    md.append("\n## Leads processed\n")
    for i, lead in enumerate(result.leads_log, 1):
        md.append(f"{i}. **[{lead.get('kind')}]** (priority={lead.get('priority')}, depth={lead.get('depth')}) {lead.get('description')}")
        md.append("")
if result.findings:
    md.append("\n## Findings (raw)\n")
    for f in result.findings:
        ev = f.get("evidence") or [{}]
        md.append(f"- **({f.get('confidence')})** {f.get('claim')}  ← {ev[0].get('tool_call_id', '?')}")
    md.append("")
```

- [ ] **Step 6: Run, expect green**

```bash
pytest tests/agents/leadqueue_v2/test_runner.py -v
```

- [ ] **Step 7: Run full suite**

```bash
pytest tests/ -q
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add osint/types.py osint/state.py osint/storage.py tests/agents/leadqueue_v2/test_runner.py
git commit -m "feat(leadqueue_v2): persist findings + leads_log through scan JSON/MD"
```

---

## Phase E — Live smoke + docs

### Task E1: Live smoke on Simon

**Files:** none modified — pure run. Expected to confirm the v2 path runs end-to-end against real APIs.

- [ ] **Step 1: Activate env**

```bash
source .venv/bin/activate
```

- [ ] **Step 2: Run a v2 scan on Simon (a sparse subject)**

```bash
python -m osint.cli scan "Simon 温行健, 高中在广州外国语学校" \
  --agent leadqueue_v2 \
  --enable web_search --enable web_extract \
  --enable apify_twitter --enable apify_instagram --enable apify_linkedin \
  2>&1 | tee /tmp/v2-simon-smoke.log
```

Expected: scan completes; `scans/<id>.json` and `scans/<id>.md` are written; the JSON contains non-empty `findings` and `leads_log`.

- [ ] **Step 3: Sanity check the report**

```bash
ls -lt scans/*.json | head -1
python -c "
import json, sys
from pathlib import Path
p = sorted(Path('scans').glob('*.json'), key=lambda x: x.stat().st_mtime)[-1]
d = json.loads(p.read_text())
print('agent:', d['config']['agent_version'])
print('status:', d['status'])
print('findings:', len(d.get('findings') or []))
print('leads_log:', len(d.get('leads_log') or []))
print('verifier iters:', d.get('verifier_iterations', 0))
print('cost: \$%.2f' % d.get('total_cost_usd', 0))
print('duration: %.0fs' % d.get('duration_sec', 0))
"
```

Expected: agent='leadqueue_v2', status='done', findings ≥ 5, leads_log ≥ 5.

- [ ] **Step 4: Compare to a v1 baseline scan on the same subject**

Run the same subject under v1 (no `--agent` flag), compare findings count + cost + duration. v2 should land at least as many findings as v1's best run, ideally more — at higher cost and longer duration.

- [ ] **Step 5: Commit any logs / notes**

```bash
mkdir -p references/v2-smoke
cp scans/<v2-id>.md references/v2-smoke/simon-v2-first-smoke.md
git add references/v2-smoke/
git commit -m "diag(v2-smoke): first lead-queue run on Simon"
```

---

### Task E2: README + final commit

**Files:**
- Modify: `README.md` (or create one if missing) — document `--agent` flag

- [ ] **Step 1: Read existing README state**

```bash
ls README*
```

- [ ] **Step 2: Add a v2 section**

If `README.md` exists, append a section:

```markdown
## Agents

The scanner has two agent versions, selectable via `--agent`:

- **`react_v1`** (default) — single ReAct loop with multi-pass deepen. Fast (~3-10 min), modest cost (~$0.30-0.70 per scan). Good for quick lookups.
- **`leadqueue_v2`** — priority-queue investigation with verifier loop. Slow (~30-60 min), higher cost (~$3-5). Designed for deep-dive scans where v1 returns shallow profiles.

Example:
```
python -m osint.cli scan "Subject Name" --agent leadqueue_v2
```

The v2 scan JSON includes two extra fields not present in v1:
- `findings` — every claim discovered, with evidence + confidence
- `leads_log` — every investigation lead processed, with kind/priority/depth
```

- [ ] **Step 3: Run full suite, expect green**

```bash
pytest tests/ -q
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README section on react_v1 vs leadqueue_v2 agent versions"
```

---

## Implementation summary

After all tasks complete:
- v1 (`react_v1`) is moved into `osint/agents/react_v1/` with NO behavior change. All existing tests pass.
- v2 (`leadqueue_v2`) is a new sub-package in `osint/agents/leadqueue_v2/` with its own queue, processor, synthesizer, verifier, and runner.
- The dispatcher in `osint/run.py` routes to either based on `config.agent_version` (CLI: `--agent`).
- `ScanState` carries v2-only fields that v1 leaves empty; serialization is backward-compatible.
- Scan JSON gains `findings` and `leads_log` arrays for v2 scans (empty for v1).
- All v2 components have unit tests; the runner has an end-to-end test with `BindableFake`.
- A live smoke run on Simon confirms the path works against real APIs.
