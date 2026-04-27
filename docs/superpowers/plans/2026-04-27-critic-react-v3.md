# critic_react_v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third agent (`critic_react_v3`) — a single-agent ReAct loop disciplined by an open-question ledger and an external adversarial critic, configurable with a free-form `goal` and a small set of canned `preset`s.

**Architecture:** One LangGraph `create_react_agent` runs to completion per "engagement"; the orchestrator parses an open-question JSON ledger from the agent's terminal message, auto-rejects if `open` is non-empty, otherwise calls a separate critic LLM that returns ACCEPT or REJECT+gaps. Reject → synthetic user message → re-engage. Cap at `max_critic_rejections`. Reuse v1's `_synthesize` for hard-cap preemption.

**Tech Stack:** Python 3.11, langgraph, langchain-core, pydantic v2, pytest, pytest-asyncio. Reuse existing `osint.state.ScanState`, `osint.capped_tool.CappedTool`, and `osint.agents.react_v1.prompts.parse_report` / `osint.agents.react_v1.runner._synthesize`.

**Source spec:** `docs/superpowers/specs/2026-04-27-critic-react-v3-design.md`. Re-read it before starting; this plan implements that spec verbatim.

---

## File map

**New:**
- `osint/agents/critic_react_v3/__init__.py` — exports `CriticReactV3Runner`.
- `osint/agents/critic_react_v3/prompts.py` — `PRESETS`, `PRESET_HINTS`, `build_system_prompt`, `parse_ledger`, `Ledger` dataclass.
- `osint/agents/critic_react_v3/critic.py` — `critic()` coroutine, `Verdict` dataclass, `parse_critic_verdict`.
- `osint/agents/critic_react_v3/runner.py` — `CriticReactV3Runner` class (outer loop).
- `tests/agents/critic_react_v3/__init__.py` — empty.
- `tests/agents/critic_react_v3/test_prompts.py`
- `tests/agents/critic_react_v3/test_critic.py`
- `tests/agents/critic_react_v3/test_runner.py`

**Modify:**
- `osint/state.py` — add `CRITIC_ACCEPTED` and `CRITIC_EXHAUSTED` to `StopReason`.
- `osint/types.py` — extend `ScanConfig` with `goal`, `preset`, `max_critic_rejections`, `max_recursion_per_engagement`.
- `osint/agents/__init__.py` — register `critic_react_v3`.
- `osint/cli.py` — add `--preset`, `--goal`, `--max-critic-rejections`, `--max-recursion-per-engagement`; expand `--agent` choices.
- `tests/test_cli.py` — assert the new flags propagate to `ScanConfig`.
- `README.md` — add a fourth bullet under "Agents".

---

## Task 1 — StopReason: add CRITIC_ACCEPTED and CRITIC_EXHAUSTED

**Files:**
- Modify: `osint/state.py:9-15`
- Test: `tests/test_state.py` (create if absent)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_state.py
from osint.state import StopReason


def test_stop_reason_has_critic_values():
    assert StopReason.CRITIC_ACCEPTED.value == "critic_accepted"
    assert StopReason.CRITIC_EXHAUSTED.value == "critic_exhausted"
```

- [ ] **Step 2: Run test, verify it fails**

```bash
.venv/bin/python -m pytest tests/test_state.py::test_stop_reason_has_critic_values -v
```
Expected: `AttributeError: CRITIC_ACCEPTED`.

- [ ] **Step 3: Add the values to the enum**

```python
# osint/state.py — replace the StopReason class body
class StopReason(str, Enum):
    NONE = "none"
    BUDGET = "budget"
    MAX_CALLS = "max_calls"
    WALL_CLOCK = "wall_clock"
    FINAL_REPORT = "final_report"
    EMPTY_FINAL = "empty_final"
    CRITIC_ACCEPTED = "critic_accepted"
    CRITIC_EXHAUSTED = "critic_exhausted"
```

- [ ] **Step 4: Run test, verify it passes**

```bash
.venv/bin/python -m pytest tests/test_state.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add osint/state.py tests/test_state.py
git commit -m "feat(state): add CRITIC_ACCEPTED and CRITIC_EXHAUSTED StopReasons"
```

---

## Task 2 — ScanConfig: add goal, preset, critic caps

**Files:**
- Modify: `osint/types.py:31-55`
- Test: `tests/test_types.py` (create if absent)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_types.py
import pytest
from pydantic import ValidationError

from osint.types import ScanConfig


def test_scan_config_defaults_for_critic_react_v3_fields():
    c = ScanConfig()
    assert c.goal == ""
    assert c.preset == "general"
    assert c.max_critic_rejections == 3
    assert c.max_recursion_per_engagement == 50


def test_scan_config_preset_must_be_known():
    with pytest.raises(ValidationError):
        ScanConfig(preset="not_a_real_preset")  # type: ignore[arg-type]


def test_scan_config_goal_accepts_free_form_string():
    c = ScanConfig(goal="coffee chat about ML infra")
    assert c.goal == "coffee chat about ML infra"
```

- [ ] **Step 2: Run test, verify it fails**

```bash
.venv/bin/python -m pytest tests/test_types.py -v
```
Expected: AttributeError or ValidationError.

- [ ] **Step 3: Extend ScanConfig**

Append the new fields to `ScanConfig` in `osint/types.py`. Keep existing fields untouched. Add the import for `Literal` at the top of the file if absent.

```python
# osint/types.py — add to imports
from typing import Any, Literal

# osint/types.py — append inside class ScanConfig (after max_processor_tool_calls)
    # Free-form goal text and named preset for critic_react_v3. Both are
    # honored; the preset preamble and the goal are concatenated into the
    # system prompt in that order. Ignored by react_v1 / leadqueue_v2 /
    # xai_multiagent_v1.
    goal: str = ""
    preset: Literal[
        "coffee_career",
        "coffee_personal",
        "reconnect",
        "sales_outreach",
        "dossier",
        "general",
    ] = "general"
    # critic_react_v3 only: cap on critic rejection rounds and per-engagement
    # LangGraph recursion limit.
    max_critic_rejections: PositiveInt = 3
    max_recursion_per_engagement: PositiveInt = 50
```

- [ ] **Step 4: Run test, verify it passes**

```bash
.venv/bin/python -m pytest tests/test_types.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add osint/types.py tests/test_types.py
git commit -m "feat(types): add goal/preset/critic-rejection knobs to ScanConfig"
```

---

## Task 3 — Package skeleton

**Files:**
- Create: `osint/agents/critic_react_v3/__init__.py`
- Create: `tests/agents/critic_react_v3/__init__.py` (empty)

- [ ] **Step 1: Create empty test package marker**

```bash
mkdir -p tests/agents/critic_react_v3
touch tests/agents/critic_react_v3/__init__.py
```

- [ ] **Step 2: Create the agent package with a placeholder export**

```python
# osint/agents/critic_react_v3/__init__.py
"""critic_react_v3 — single ReAct agent + open-question ledger + adversarial critic."""

from osint.agents.critic_react_v3.runner import CriticReactV3Runner

__all__ = ["CriticReactV3Runner"]
```

This will fail to import until Task 9 lands `runner.py`. We'll fix imports as we go; for now, do not add to `osint/agents/__init__.py` yet.

- [ ] **Step 3: Commit (skeleton only — no runner yet, no AGENTS wiring)**

Skip commit until Task 9 lands the runner; package import is broken without it.

---

## Task 4 — prompts.py: PRESETS dict and PRESET_HINTS

**Files:**
- Create: `osint/agents/critic_react_v3/prompts.py`
- Test: `tests/agents/critic_react_v3/test_prompts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/critic_react_v3/test_prompts.py
import pytest

from osint.agents.critic_react_v3.prompts import PRESETS, PRESET_HINTS


def test_presets_cover_all_six_names():
    assert set(PRESETS) == {
        "coffee_career", "coffee_personal", "reconnect",
        "sales_outreach", "dossier", "general",
    }


def test_preset_hints_cover_all_six_names():
    assert set(PRESET_HINTS) == set(PRESETS)


@pytest.mark.parametrize("name", list({"coffee_career","coffee_personal","reconnect","sales_outreach","dossier","general"}))
def test_preset_preamble_is_nonempty_string(name):
    assert isinstance(PRESETS[name], str) and PRESETS[name].strip()


@pytest.mark.parametrize("name", list({"coffee_career","coffee_personal","reconnect","sales_outreach","dossier","general"}))
def test_preset_hint_is_short_one_liner(name):
    assert isinstance(PRESET_HINTS[name], str) and PRESET_HINTS[name].strip()
    assert len(PRESET_HINTS[name]) < 200
```

- [ ] **Step 2: Run, verify failure**

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_prompts.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement prompts.py — PRESETS + PRESET_HINTS only**

```python
# osint/agents/critic_react_v3/prompts.py
"""Prompt builders, preset library, and ledger parser for critic_react_v3.

Presets are canned `goal` preambles. The user's free-form `goal` (if any)
is appended after the preset preamble in the system prompt.

PRESET_HINTS is a one-line summary of each preset, used by the critic
prompt — the critic doesn't need the full preamble, just enough to know
what kind of investigation it's evaluating.
"""
from __future__ import annotations


PRESETS: dict[str, str] = {
    "coffee_career": (
        "I'm preparing for a coffee chat with this person, focused on their career. "
        "Find their current role and employer, recent shipped work or projects, "
        "any public talks/posts/papers worth referencing, and shared interests "
        "that might come up. Flag anything sensitive to avoid (recent layoff, "
        "controversy, loss). Skip family, addresses, and history older than ~5y "
        "unless directly relevant."
    ),
    "coffee_personal": (
        "I want to know this person better as a friend or new acquaintance. "
        "Find their hobbies, interests, communities they're part of, and recent "
        "public posts I could react to. Skip employment-financial details, "
        "addresses, and anything that feels invasive."
    ),
    "reconnect": (
        "I want to reconnect with this person after time apart. Find what "
        "they've been doing recently — new role, new city, life events, "
        "projects — so I can open the conversation naturally."
    ),
    "sales_outreach": (
        "I'm preparing outreach to this person about a business matter. "
        "Find their company, role, recent public communications, mutual "
        "connections, and topics they care about that I can reference warmly."
    ),
    "dossier": (
        "Build a comprehensive dossier. Be thorough across identity, career, "
        "education, online footprint, network, geography, and history. "
        "Surface concrete identifiers and follow up on each."
    ),
    "general": (
        "Investigate this person with whatever lens makes sense from the "
        "subject description and any user-provided goal."
    ),
}


PRESET_HINTS: dict[str, str] = {
    "coffee_career": "career-focused coffee chat: current role, recent work, talking points, things to avoid.",
    "coffee_personal": "personal coffee chat: hobbies, communities, recent posts, no invasive details.",
    "reconnect": "reconnect with old contact: recent moves, life events, conversation openers.",
    "sales_outreach": "warm outreach: company, role, recent public comms, mutual connections.",
    "dossier": "comprehensive dossier: identity, career, education, footprint, network, history.",
    "general": "free-form investigation guided by the user's goal text.",
}
```

- [ ] **Step 4: Run, verify pass**

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_prompts.py -v
```
Expected: 14 PASS (4 unparametrized + 12 parametrized).

- [ ] **Step 5: Commit**

```bash
git add osint/agents/critic_react_v3/prompts.py tests/agents/critic_react_v3/__init__.py tests/agents/critic_react_v3/test_prompts.py
git commit -m "feat(critic_react_v3): preset library + preset hints"
```

---

## Task 5 — prompts.py: build_system_prompt

**Files:**
- Modify: `osint/agents/critic_react_v3/prompts.py`
- Modify: `tests/agents/critic_react_v3/test_prompts.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/agents/critic_react_v3/test_prompts.py`:

```python
from osint.agents.critic_react_v3.prompts import build_system_prompt


def test_build_system_prompt_contains_subject_and_preset_preamble():
    p = build_system_prompt(
        subject="Jane Doe",
        goal="",
        preset="coffee_career",
        tool_names=["web_search", "web_extract"],
    )
    assert "Jane Doe" in p
    assert "coffee chat" in p.lower()
    assert "web_search" in p
    assert "web_extract" in p


def test_build_system_prompt_appends_goal_text_after_preset():
    p = build_system_prompt(
        subject="Jane Doe",
        goal="Meeting to discuss her transformer-inference work",
        preset="coffee_career",
        tool_names=["web_search"],
    )
    assert "transformer-inference" in p
    coffee_idx = p.lower().find("coffee chat")
    goal_idx = p.find("transformer-inference")
    assert coffee_idx < goal_idx, "goal text must appear after preset preamble"


def test_build_system_prompt_general_with_empty_goal_still_valid():
    p = build_system_prompt(
        subject="Jane Doe",
        goal="",
        preset="general",
        tool_names=["web_search"],
    )
    assert "Jane Doe" in p
    assert "Investigate" in p


def test_build_system_prompt_states_parallelism_rule():
    p = build_system_prompt(
        subject="Jane Doe", goal="", preset="general",
        tool_names=["web_search"],
    )
    assert "parallel" in p.lower() or "batch" in p.lower()


def test_build_system_prompt_states_ledger_rule():
    p = build_system_prompt(
        subject="Jane Doe", goal="", preset="general",
        tool_names=["web_search"],
    )
    assert "open" in p.lower() and "ledger" in p.lower() or '"open"' in p


def test_build_system_prompt_states_final_report_envelope():
    p = build_system_prompt(
        subject="Jane Doe", goal="", preset="general",
        tool_names=["web_search"],
    )
    assert "extracted_identifiers" in p
```

- [ ] **Step 2: Run, verify failure**

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_prompts.py -v
```
Expected: ImportError on `build_system_prompt`.

- [ ] **Step 3: Implement build_system_prompt**

Append to `osint/agents/critic_react_v3/prompts.py`:

```python
_SYSTEM_TEMPLATE = """\
You are EliteOSINT, an open-source intelligence analyst. Investigate the
following SUBJECT to satisfy the GOAL using the available tools.

SUBJECT:
{subject}

GOAL:
{goal_block}

AVAILABLE TOOLS:
{tools_block}

RULES OF ENGAGEMENT:

1. PARALLELISM. When two or more tool calls are independent (do not depend
   on each other's output), emit them as a single batch in one assistant
   message. Sequential single calls when batching is possible is a defect.
   Examples of independent calls: searching multiple distinct queries;
   fetching multiple URLs; probing a handle on different platforms.

2. OPEN-QUESTION LEDGER. Begin every assistant message with a fenced JSON
   block of this exact shape:
   ```json
   {{"open": [], "answered": [], "dropped": []}}
   ```
   - `open`: free-form questions you still need to answer.
   - `answered`: questions you've answered, each with a brief evidence pointer.
   - `dropped`: questions you've decided not to pursue, with a brief reason.
   You MAY NOT terminate while `open` is non-empty.

3. STOP DISCIPLINE. Never stop if a finding contains a concrete identifier
   (email, handle, URL, platform user id) that has not been followed up on.
   Such an identifier is an unanswered open question by definition.

4. FINAL REPORT. When (and only when) `open` is empty, emit your final
   report as free-form prose, followed by EXACTLY ONE fenced JSON block
   keyed `extracted_identifiers` with this shape:
   ```json
   {{
     "extracted_identifiers": {{
       "emails": [],
       "usernames": [],
       "urls": [],
       "name_variations": [],
       "schools": [],
       "employers": [],
       "phones": [],
       "addresses": []
     }}
   }}
   ```

Use Google search syntax in web_search (quoted phrases, OR, site:, intitle:,
filetype:). Read every snippet word-for-word — handles, emails, and project
names commonly leak inline. Cite tool calls in your prose.
"""


def build_system_prompt(
    *,
    subject: str,
    goal: str,
    preset: str,
    tool_names: list[str],
) -> str:
    """Build the system prompt for one engagement.

    The preset preamble and the user-supplied goal are concatenated in
    that order under GOAL. Either may be empty; if both are, GOAL is
    just the preset's preamble. `preset` must be a key of `PRESETS` —
    callers should validate (Pydantic Literal already does at config time).
    """
    preamble = PRESETS.get(preset, PRESETS["general"])
    goal_block = preamble if not goal.strip() else f"{preamble}\n\nUser-specific goal: {goal.strip()}"
    tools_block = "\n".join(f"- {n}" for n in tool_names) if tool_names else "- (no tools enabled)"
    return _SYSTEM_TEMPLATE.format(
        subject=subject,
        goal_block=goal_block,
        tools_block=tools_block,
    )
```

- [ ] **Step 4: Run, verify pass**

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_prompts.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add osint/agents/critic_react_v3/prompts.py tests/agents/critic_react_v3/test_prompts.py
git commit -m "feat(critic_react_v3): build_system_prompt with parallelism+ledger rules"
```

---

## Task 6 — prompts.py: parse_ledger

**Files:**
- Modify: `osint/agents/critic_react_v3/prompts.py`
- Modify: `tests/agents/critic_react_v3/test_prompts.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/agents/critic_react_v3/test_prompts.py`:

```python
from osint.agents.critic_react_v3.prompts import Ledger, parse_ledger


def test_parse_ledger_well_formed():
    text = (
        "```json\n"
        '{"open": ["Q1", "Q2"], "answered": ["A1"], "dropped": []}\n'
        "```\nThe rest of the report."
    )
    led = parse_ledger(text)
    assert led.open == ["Q1", "Q2"]
    assert led.answered == ["A1"]
    assert led.dropped == []


def test_parse_ledger_empty_lists_when_keys_missing():
    text = '```json\n{"open": []}\n```'
    led = parse_ledger(text)
    assert led.open == []
    assert led.answered == []
    assert led.dropped == []


def test_parse_ledger_no_block_returns_empty_ledger():
    led = parse_ledger("No JSON block at all in this text.")
    assert led.open == []
    assert led.answered == []
    assert led.dropped == []


def test_parse_ledger_malformed_json_returns_empty_ledger():
    text = '```json\n{"open": [bad}\n```'
    led = parse_ledger(text)
    assert led.open == []
    assert led.answered == []
    assert led.dropped == []


def test_parse_ledger_picks_first_json_block_only():
    """The first JSON block IS the ledger; later blocks (e.g. extracted_identifiers)
    must not be parsed as a ledger."""
    text = (
        '```json\n{"open": ["Q1"]}\n```\n'
        'Some prose.\n'
        '```json\n{"extracted_identifiers": {"emails": []}}\n```'
    )
    led = parse_ledger(text)
    assert led.open == ["Q1"]
```

- [ ] **Step 2: Run, verify failure**

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_prompts.py -v
```
Expected: ImportError on `parse_ledger` / `Ledger`.

- [ ] **Step 3: Implement parse_ledger and Ledger**

Append to `osint/agents/critic_react_v3/prompts.py`:

```python
import json
import re
from dataclasses import dataclass, field


_FENCED_JSON_FIRST = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class Ledger:
    """Open-question ledger parsed from an assistant message.

    Empty lists everywhere when no parsable ledger is present — callers
    treat that case as "no open questions" so a malformed ledger never
    blocks termination.
    """
    open: list[str] = field(default_factory=list)
    answered: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def parse_ledger(text: str) -> Ledger:
    """Parse the first fenced ```json``` block at the head of `text`.

    Returns an empty Ledger if no block is present or the block is
    malformed JSON or not an object.
    """
    if not text:
        return Ledger()
    m = _FENCED_JSON_FIRST.search(text)
    if not m:
        return Ledger()
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return Ledger()
    if not isinstance(data, dict):
        return Ledger()
    def _list(key: str) -> list[str]:
        v = data.get(key) or []
        return [str(x) for x in v] if isinstance(v, list) else []
    return Ledger(open=_list("open"), answered=_list("answered"), dropped=_list("dropped"))
```

- [ ] **Step 4: Run, verify pass**

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_prompts.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add osint/agents/critic_react_v3/prompts.py tests/agents/critic_react_v3/test_prompts.py
git commit -m "feat(critic_react_v3): parse_ledger + Ledger dataclass"
```

---

## Task 7 — critic.py: parse_critic_verdict

**Files:**
- Create: `osint/agents/critic_react_v3/critic.py`
- Create: `tests/agents/critic_react_v3/test_critic.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/agents/critic_react_v3/test_critic.py
from osint.agents.critic_react_v3.critic import Verdict, parse_critic_verdict


def test_accept_verdict():
    v = parse_critic_verdict("VERDICT: ACCEPT\n")
    assert v.accept is True
    assert v.gaps == []


def test_reject_with_bullets():
    text = (
        "VERDICT: REJECT\n"
        "GAPS:\n"
        "- Subject's current employer not confirmed\n"
        "- Email fc202817@bunka-fc.ac.jp never followed up via web_search\n"
    )
    v = parse_critic_verdict(text)
    assert v.accept is False
    assert v.gaps == [
        "Subject's current employer not confirmed",
        "Email fc202817@bunka-fc.ac.jp never followed up via web_search",
    ]


def test_reject_without_bullets_still_rejected_but_empty_gaps():
    v = parse_critic_verdict("VERDICT: REJECT\n")
    assert v.accept is False
    assert v.gaps == []


def test_malformed_treated_as_accept():
    v = parse_critic_verdict("nonsense, no verdict line at all")
    assert v.accept is True
    assert v.gaps == []


def test_verdict_case_insensitive():
    assert parse_critic_verdict("verdict: accept").accept is True
    assert parse_critic_verdict("Verdict: Reject\nGAPS:\n- X").accept is False
```

- [ ] **Step 2: Run, verify failure**

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_critic.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement parse_critic_verdict + Verdict**

```python
# osint/agents/critic_react_v3/critic.py
"""Critic call + verdict parser for critic_react_v3.

The critic is one LLM invocation, no tools. It reads the goal, the
agent's draft report, and a tool-call summary, and returns either
ACCEPT or REJECT with a list of gaps. Parser failures default to
ACCEPT to avoid infinite loops on parser fragility (per spec).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from osint.agents.critic_react_v3.prompts import PRESET_HINTS


@dataclass
class Verdict:
    accept: bool
    gaps: list[str] = field(default_factory=list)


_VERDICT_RE = re.compile(r"VERDICT\s*:\s*(ACCEPT|REJECT)", re.IGNORECASE)


def parse_critic_verdict(text: str) -> Verdict:
    """Parse the critic's free-form output into a Verdict.

    Format expected:
        VERDICT: ACCEPT | REJECT
        GAPS:
        - bullet 1
        - bullet 2

    Missing/malformed VERDICT line → treat as ACCEPT (avoid infinite loops).
    """
    if not text:
        return Verdict(accept=True)
    m = _VERDICT_RE.search(text)
    if not m:
        return Verdict(accept=True)
    decision = m.group(1).upper()
    if decision == "ACCEPT":
        return Verdict(accept=True)
    # REJECT — collect bullets after a "GAPS:" header (case-insensitive).
    lines = text.splitlines()
    gaps: list[str] = []
    in_gaps = False
    for line in lines:
        if re.match(r"^\s*GAPS\s*:", line, re.IGNORECASE):
            in_gaps = True
            continue
        if in_gaps:
            stripped = line.strip()
            if stripped.startswith("- "):
                gaps.append(stripped[2:].strip())
    return Verdict(accept=False, gaps=gaps)
```

- [ ] **Step 4: Run, verify pass**

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_critic.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add osint/agents/critic_react_v3/critic.py tests/agents/critic_react_v3/test_critic.py
git commit -m "feat(critic_react_v3): parse_critic_verdict + Verdict"
```

---

## Task 8 — critic.py: critic() coroutine

**Files:**
- Modify: `osint/agents/critic_react_v3/critic.py`
- Modify: `tests/agents/critic_react_v3/test_critic.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/agents/critic_react_v3/test_critic.py`:

```python
from unittest.mock import MagicMock

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from osint.agents.critic_react_v3.critic import critic


async def test_critic_returns_accept_verdict():
    fake = FakeMessagesListChatModel(responses=[AIMessage(content="VERDICT: ACCEPT\n")])
    v = await critic(
        subject="Jane",
        goal="coffee chat about ML",
        preset="coffee_career",
        draft="Jane works at Acme on ML infra...",
        tool_calls=[],
        llm=fake,
        cost_cb=MagicMock(),
    )
    assert v.accept is True
    assert v.gaps == []


async def test_critic_returns_reject_with_gaps():
    fake = FakeMessagesListChatModel(responses=[AIMessage(
        content="VERDICT: REJECT\nGAPS:\n- No current role\n- Email never probed\n"
    )])
    v = await critic(
        subject="Jane",
        goal="",
        preset="dossier",
        draft="Jane lives in Tokyo.",
        tool_calls=[],
        llm=fake,
        cost_cb=MagicMock(),
    )
    assert v.accept is False
    assert v.gaps == ["No current role", "Email never probed"]
```

- [ ] **Step 2: Run, verify failure**

Expected: ImportError on `critic`.

- [ ] **Step 3: Implement critic()**

Append to `osint/agents/critic_react_v3/critic.py`:

```python
_CRITIC_SYSTEM = """\
You are reviewing whether an investigation has met its goal.

Decide: accept the draft, or reject with specific gaps the investigator
should address. A gap is something the goal needs that the draft does
not currently support, OR a concrete identifier in the draft (email,
handle, url, id) that was never followed up on.

Respond in this exact form:

VERDICT: ACCEPT
or
VERDICT: REJECT
GAPS:
- (one bullet per gap)
"""


def _summarize_tool_calls(tool_calls: list[Any]) -> str:
    """One-line histogram of tool-call counts by tool name. Empty when no calls."""
    if not tool_calls:
        return "(no tool calls were made)"
    counts: dict[str, int] = {}
    for tc in tool_calls:
        name = getattr(tc, "tool", None) or (tc.get("tool") if isinstance(tc, dict) else None) or "unknown"
        counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{n}={c}" for n, c in sorted(counts.items()))


async def critic(
    *,
    subject: str,
    goal: str,
    preset: str,
    draft: str,
    tool_calls: list[Any],
    llm: BaseChatModel,
    cost_cb: Any,
) -> Verdict:
    """Single LLM call, no tools. Returns Verdict.

    Parser failures default to ACCEPT (parse_critic_verdict policy).
    Network/API errors propagate — caller decides whether to retry.
    """
    user_msg = (
        f"GOAL: {goal or '(none — use preset hint)'}\n"
        f"PRESET HINT: {PRESET_HINTS.get(preset, PRESET_HINTS['general'])}\n"
        f"SUBJECT: {subject}\n\n"
        f"TOOLS USED (count by name): {_summarize_tool_calls(tool_calls)}\n\n"
        f"DRAFT REPORT:\n{draft}"
    )
    callbacks = [cost_cb] if isinstance(cost_cb, BaseCallbackHandler) else []
    resp = await llm.ainvoke(
        [SystemMessage(content=_CRITIC_SYSTEM), HumanMessage(content=user_msg)],
        config={"callbacks": callbacks},
    )
    return parse_critic_verdict(getattr(resp, "content", "") or "")
```

- [ ] **Step 4: Run, verify pass**

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_critic.py -v
```
Expected: all PASS (sync + async).

- [ ] **Step 5: Commit**

```bash
git add osint/agents/critic_react_v3/critic.py tests/agents/critic_react_v3/test_critic.py
git commit -m "feat(critic_react_v3): critic() coroutine"
```

---

## Task 9 — runner.py: CriticReactV3Runner happy path

**Files:**
- Create: `osint/agents/critic_react_v3/runner.py`
- Create: `tests/agents/critic_react_v3/test_runner.py`

- [ ] **Step 1: Write failing test**

```python
# tests/agents/critic_react_v3/test_runner.py
"""End-to-end tests for CriticReactV3Runner using BindableFake.

The runner alternates create_react_agent.ainvoke() (which consumes one
LLM response per call when the model emits no tool_calls) with critic()
(also one LLM response per call). Tests pin the engagement→critic cycle.
"""
from unittest.mock import MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from osint.agents.critic_react_v3.runner import CriticReactV3Runner
from osint.state import ScanState, StopReason
from osint.types import ScanConfig


class BindableFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self


HAPPY_DRAFT = (
    '```json\n{"open": [], "answered": ["Q1"], "dropped": []}\n```\n\n'
    '**Executive Summary**\n\nJane works at Acme.\n\n'
    '```json\n{"extracted_identifiers": {"employers": ["Acme"]}}\n```'
)


async def test_runner_happy_path_first_engagement_accepted():
    """Engagement 1 emits empty-`open` ledger + final report -> critic ACCEPT -> done."""
    fake = BindableFake(responses=[
        AIMessage(content=HAPPY_DRAFT, tool_calls=[]),  # engagement 1 final
        AIMessage(content="VERDICT: ACCEPT\n"),         # critic
    ])
    state = ScanState(
        scan_id="x",
        subject="Jane",
        config=ScanConfig(agent_version="critic_react_v3", preset="coffee_career"),
    )
    runner = CriticReactV3Runner()
    parsed, stop_reason = await runner.run(
        subject="Jane",
        state=state,
        llm=fake,
        tools=[],
        cost_cb=MagicMock(),
    )
    assert stop_reason is None
    assert parsed["extracted_identifiers"] == {"employers": ["Acme"]}
    assert "Jane works at Acme" in parsed["report"]["text"]
```

- [ ] **Step 2: Run, verify failure**

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_runner.py -v
```
Expected: ImportError on `CriticReactV3Runner`.

- [ ] **Step 3: Implement runner.py — happy path only**

```python
# osint/agents/critic_react_v3/runner.py
"""CriticReactV3Runner — ReAct agent + open-question ledger + adversarial critic.

Outer loop:
  1. Build system prompt from subject + preset + goal + tools.
  2. Run a fresh create_react_agent to terminal AIMessage (one engagement).
  3. Parse the open-question ledger from the terminal AIMessage.
     - If `open` non-empty: append synthetic "you have open questions" user
       message and re-run from step 2.
  4. Otherwise call the critic. ACCEPT -> done. REJECT -> append "reviewer
     flagged these gaps" message and re-run from step 2.
  5. Cap critic rejections at config.max_critic_rejections.

Hard caps (budget, max_calls, wall_clock) preempt the loop. On preemption
fall through to v1's _synthesize so the user always gets *some* report.
"""
from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from osint.agents.critic_react_v3.critic import critic
from osint.agents.critic_react_v3.prompts import build_system_prompt, parse_ledger
from osint.agents.react_v1.prompts import parse_report
from osint.agents.react_v1.runner import _serialize_messages, _synthesize
from osint.errors import ScanStopped
from osint.state import ScanState, StopReason


def _extract_last_ai_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            return m.content or ""
    return ""


class CriticReactV3Runner:
    """v3 agent — ReAct + ledger + critic. Implements osint.agents.base.AgentRunner."""

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
        system_text = build_system_prompt(
            subject=subject,
            goal=config.goal,
            preset=config.preset,
            tool_names=sorted(config.enabled_tools),
        )
        messages: list[BaseMessage] = [
            SystemMessage(content=system_text),
            HumanMessage(content="Begin."),
        ]
        invoke_callbacks = [cost_cb] if isinstance(cost_cb, BaseCallbackHandler) else []
        rejections = 0
        last_text = ""
        last_stop_reason: StopReason | None = None

        while True:
            stopped, reason = state.should_stop()
            if stopped:
                last_stop_reason = reason
                break
            if rejections > config.max_critic_rejections:
                last_stop_reason = StopReason.CRITIC_EXHAUSTED
                break

            agent = create_react_agent(llm, tools, prompt=None)
            try:
                agent_result = await asyncio.wait_for(
                    agent.ainvoke(
                        {"messages": messages},
                        config={
                            "recursion_limit": config.max_recursion_per_engagement,
                            "callbacks": invoke_callbacks,
                        },
                    ),
                    timeout=config.max_wall_clock_sec,
                )
            except ScanStopped as e:
                last_stop_reason = StopReason(e.reason)
                break
            except asyncio.TimeoutError:
                last_stop_reason = StopReason.WALL_CLOCK
                break
            except GraphRecursionError:
                last_stop_reason = StopReason.MAX_CALLS
                break

            messages = list(agent_result.get("messages", []))
            last_text = _extract_last_ai_text(messages)
            if not last_text.strip():
                last_stop_reason = StopReason.EMPTY_FINAL
                break

            ledger = parse_ledger(last_text)
            if ledger.open:
                messages.append(HumanMessage(content=(
                    f"You stopped with open questions: {ledger.open}. "
                    f"Continue investigating; you may use any tools. "
                    f"Update your open-question ledger before any final report."
                )))
                continue

            verdict = await critic(
                subject=subject,
                goal=config.goal,
                preset=config.preset,
                draft=last_text,
                tool_calls=state.tool_calls,
                llm=llm,
                cost_cb=cost_cb,
            )
            if verdict.accept:
                last_stop_reason = StopReason.CRITIC_ACCEPTED
                break
            rejections += 1
            messages.append(HumanMessage(content=(
                "A reviewer flagged these gaps:\n- " + "\n- ".join(verdict.gaps) +
                "\n\nAddress each. Use any tools. Update your open-question ledger; "
                "remember the final-report JSON envelope shape."
            )))

        # Cap-cut path on preemption / empty final.
        cap_cut_reasons = {
            StopReason.BUDGET, StopReason.MAX_CALLS,
            StopReason.WALL_CLOCK, StopReason.EMPTY_FINAL,
        }
        state.messages.extend(_serialize_messages(messages))
        if last_stop_reason in cap_cut_reasons:
            synth_text, synth_msgs = await _synthesize(
                llm, subject, state, last_stop_reason.value, cost_cb,
            )
            state.messages.extend(_serialize_messages(synth_msgs))
            parsed = parse_report(synth_text)
            state.record_final_report(
                parsed.get("report") or {},
                identifiers=parsed.get("extracted_identifiers") or {},
            )
            return parsed, last_stop_reason

        # Critic-accepted or critic-exhausted: parse the last engagement's draft.
        parsed = parse_report(last_text)
        state.record_final_report(
            parsed.get("report") or {},
            identifiers=parsed.get("extracted_identifiers") or {},
        )
        # CRITIC_ACCEPTED is a clean finish — return None for stop_reason.
        return parsed, None if last_stop_reason == StopReason.CRITIC_ACCEPTED else last_stop_reason
```

- [ ] **Step 4: Run, verify pass**

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_runner.py::test_runner_happy_path_first_engagement_accepted -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add osint/agents/critic_react_v3/__init__.py osint/agents/critic_react_v3/runner.py tests/agents/critic_react_v3/test_runner.py
git commit -m "feat(critic_react_v3): runner happy path (engagement -> critic ACCEPT)"
```

---

## Task 10 — runner: ledger non-empty triggers retry

**Files:**
- Modify: `tests/agents/critic_react_v3/test_runner.py`

- [ ] **Step 1: Add failing test**

Append to `tests/agents/critic_react_v3/test_runner.py`:

```python
LEDGER_NONEMPTY_DRAFT = (
    '```json\n{"open": ["What is current employer?"], "answered": [], "dropped": []}\n```\n\n'
    'Partial findings...'
)


async def test_runner_ledger_non_empty_retries_then_accepts():
    """Engagement 1 emits non-empty `open` -> orchestrator appends synthetic
    user msg -> Engagement 2 emits empty `open` + final report -> critic ACCEPT."""
    fake = BindableFake(responses=[
        AIMessage(content=LEDGER_NONEMPTY_DRAFT, tool_calls=[]),  # engagement 1 (still open)
        AIMessage(content=HAPPY_DRAFT, tool_calls=[]),            # engagement 2 (empty open)
        AIMessage(content="VERDICT: ACCEPT\n"),                   # critic
    ])
    state = ScanState(
        scan_id="x", subject="Jane",
        config=ScanConfig(agent_version="critic_react_v3"),
    )
    runner = CriticReactV3Runner()
    parsed, stop_reason = await runner.run(
        subject="Jane", state=state, llm=fake, tools=[], cost_cb=MagicMock(),
    )
    assert stop_reason is None
    assert parsed["extracted_identifiers"] == {"employers": ["Acme"]}
```

- [ ] **Step 2: Run, verify pass**

(The runner already implements this path — test should pass on first run.)

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_runner.py::test_runner_ledger_non_empty_retries_then_accepts -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/agents/critic_react_v3/test_runner.py
git commit -m "test(critic_react_v3): runner retries on non-empty open-question ledger"
```

---

## Task 11 — runner: critic REJECT triggers retry

**Files:**
- Modify: `tests/agents/critic_react_v3/test_runner.py`

- [ ] **Step 1: Add failing test**

Append:

```python
HAPPY_DRAFT_2 = (
    '```json\n{"open": [], "answered": ["Q1","Q2"], "dropped": []}\n```\n\n'
    '**Executive Summary**\n\nJane works at Acme; current title VP Eng.\n\n'
    '```json\n{"extracted_identifiers": {"employers": ["Acme"], "name_variations": ["Jane Doe"]}}\n```'
)


async def test_runner_critic_reject_then_accept_after_one_revision():
    """Engagement 1 empty-open -> critic REJECT -> engagement 2 empty-open -> critic ACCEPT."""
    fake = BindableFake(responses=[
        AIMessage(content=HAPPY_DRAFT, tool_calls=[]),                          # engagement 1
        AIMessage(content="VERDICT: REJECT\nGAPS:\n- No title for current role\n"),  # critic 1
        AIMessage(content=HAPPY_DRAFT_2, tool_calls=[]),                        # engagement 2
        AIMessage(content="VERDICT: ACCEPT\n"),                                 # critic 2
    ])
    state = ScanState(
        scan_id="x", subject="Jane",
        config=ScanConfig(agent_version="critic_react_v3", max_critic_rejections=3),
    )
    parsed, stop_reason = await CriticReactV3Runner().run(
        subject="Jane", state=state, llm=fake, tools=[], cost_cb=MagicMock(),
    )
    assert stop_reason is None
    assert "VP Eng" in parsed["report"]["text"]
```

- [ ] **Step 2: Run, verify pass**

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_runner.py::test_runner_critic_reject_then_accept_after_one_revision -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/agents/critic_react_v3/test_runner.py
git commit -m "test(critic_react_v3): runner re-engages on critic REJECT"
```

---

## Task 12 — runner: critic exhaustion returns last draft

**Files:**
- Modify: `tests/agents/critic_react_v3/test_runner.py`

- [ ] **Step 1: Add failing test**

Append:

```python
async def test_runner_critic_exhaustion_returns_last_draft_with_critic_exhausted():
    """max_critic_rejections=1: 1 engagement, REJECT, 1 more engagement, REJECT
    -> rejections=2 > 1 -> return last draft with CRITIC_EXHAUSTED."""
    fake = BindableFake(responses=[
        AIMessage(content=HAPPY_DRAFT, tool_calls=[]),                # engagement 1
        AIMessage(content="VERDICT: REJECT\nGAPS:\n- gap a\n"),       # critic 1 (rejection 1)
        AIMessage(content=HAPPY_DRAFT_2, tool_calls=[]),              # engagement 2
        AIMessage(content="VERDICT: REJECT\nGAPS:\n- gap b\n"),       # critic 2 (rejection 2 > cap)
    ])
    state = ScanState(
        scan_id="x", subject="Jane",
        config=ScanConfig(agent_version="critic_react_v3", max_critic_rejections=1),
    )
    parsed, stop_reason = await CriticReactV3Runner().run(
        subject="Jane", state=state, llm=fake, tools=[], cost_cb=MagicMock(),
    )
    assert stop_reason == StopReason.CRITIC_EXHAUSTED
    # Returned draft is the LAST one the agent produced.
    assert "VP Eng" in parsed["report"]["text"]
    assert parsed["extracted_identifiers"]["employers"] == ["Acme"]
```

- [ ] **Step 2: Run, verify pass**

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_runner.py::test_runner_critic_exhaustion_returns_last_draft_with_critic_exhausted -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/agents/critic_react_v3/test_runner.py
git commit -m "test(critic_react_v3): runner returns last draft on critic exhaustion"
```

---

## Task 13 — runner: cap-cut on EMPTY_FINAL

**Files:**
- Modify: `tests/agents/critic_react_v3/test_runner.py`

- [ ] **Step 1: Add failing test**

Append:

```python
async def test_runner_empty_final_falls_through_to_cap_cut_synthesis():
    """If the agent emits an empty AIMessage as terminal, runner falls
    through to v1's _synthesize. The synthesizer's response becomes the
    final report."""
    SYNTH_FALLBACK = (
        '**Executive Summary**\n\nJane (cap-cut).\n\n'
        '```json\n{"extracted_identifiers": {"employers": ["Acme"]}}\n```'
    )
    fake = BindableFake(responses=[
        AIMessage(content="", tool_calls=[]),               # engagement 1 EMPTY -> EMPTY_FINAL
        AIMessage(content=SYNTH_FALLBACK, tool_calls=[]),   # _synthesize
    ])
    state = ScanState(
        scan_id="x", subject="Jane",
        config=ScanConfig(agent_version="critic_react_v3"),
    )
    parsed, stop_reason = await CriticReactV3Runner().run(
        subject="Jane", state=state, llm=fake, tools=[], cost_cb=MagicMock(),
    )
    assert stop_reason == StopReason.EMPTY_FINAL
    assert "cap-cut" in parsed["report"]["text"]
    assert parsed["extracted_identifiers"]["employers"] == ["Acme"]
```

- [ ] **Step 2: Run, verify pass**

```bash
.venv/bin/python -m pytest tests/agents/critic_react_v3/test_runner.py::test_runner_empty_final_falls_through_to_cap_cut_synthesis -v
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/agents/critic_react_v3/test_runner.py
git commit -m "test(critic_react_v3): runner cap-cut synthesis on EMPTY_FINAL"
```

---

## Task 14 — Wire into AGENTS dispatch

**Files:**
- Modify: `osint/agents/__init__.py`
- Test: `tests/test_dispatcher.py` (extend existing)

- [ ] **Step 1: Add failing test**

Append to `tests/test_dispatcher.py` (or create the file with the test below if it doesn't exist):

```python
from osint.agents import AGENTS
from osint.agents.critic_react_v3 import CriticReactV3Runner


def test_critic_react_v3_registered():
    assert "critic_react_v3" in AGENTS
    assert AGENTS["critic_react_v3"] is CriticReactV3Runner
```

- [ ] **Step 2: Run, verify failure**

```bash
.venv/bin/python -m pytest tests/test_dispatcher.py::test_critic_react_v3_registered -v
```
Expected: KeyError or ImportError.

- [ ] **Step 3: Register in AGENTS**

Replace `osint/agents/__init__.py`:

```python
"""Agent runner registry. Map agent_version (str) → AgentRunner class."""
from osint.agents.critic_react_v3 import CriticReactV3Runner
from osint.agents.leadqueue_v2 import LeadQueueV2Runner
from osint.agents.react_v1 import ReactV1Runner
from osint.agents.xai_multiagent_v1 import XaiMultiAgentV1Runner

AGENTS = {
    "react_v1": ReactV1Runner,
    "leadqueue_v2": LeadQueueV2Runner,
    "xai_multiagent_v1": XaiMultiAgentV1Runner,
    "critic_react_v3": CriticReactV3Runner,
}
```

- [ ] **Step 4: Run, verify pass**

```bash
.venv/bin/python -m pytest tests/test_dispatcher.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add osint/agents/__init__.py tests/test_dispatcher.py
git commit -m "feat(agents): register critic_react_v3 in AGENTS dispatch"
```

---

## Task 15 — CLI flags

**Files:**
- Modify: `osint/cli.py:32-66`, `osint/cli.py:171-188`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_cli.py`:

```python
from osint.cli import _build_args


def test_cli_accepts_critic_react_v3_agent():
    args = _build_args(["scan", "Jane", "--agent", "critic_react_v3"])
    assert args.agent == "critic_react_v3"


def test_cli_accepts_preset_and_goal():
    args = _build_args([
        "scan", "Jane",
        "--agent", "critic_react_v3",
        "--preset", "coffee_career",
        "--goal", "We're meeting about ML infra",
    ])
    assert args.preset == "coffee_career"
    assert args.goal == "We're meeting about ML infra"


def test_cli_accepts_critic_caps():
    args = _build_args([
        "scan", "Jane",
        "--max-critic-rejections", "5",
        "--max-recursion-per-engagement", "100",
    ])
    assert args.max_critic_rejections == 5
    assert args.max_recursion_per_engagement == 100


def test_cli_preset_default_is_general_when_omitted():
    args = _build_args(["scan", "Jane"])
    assert args.preset == "general"
    assert args.goal == ""
```

- [ ] **Step 2: Run, verify failure**

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```
Expected: argparse error / AttributeError.

- [ ] **Step 3: Edit `osint/cli.py`**

Inside `_build_parser`, change the `--agent` choices line to include the new value, and add the four new flags.

Replace the `--agent` argument block:

```python
    s.add_argument(
        "--agent",
        choices=["react_v1", "leadqueue_v2", "xai_multiagent_v1", "critic_react_v3"],
        default="react_v1",
        help="Agent runner. react_v1 = ReAct loop with multi-pass deepen "
             "(default; behaves like before this flag existed). "
             "leadqueue_v2 = priority-queue investigation with verifier loop. "
             "xai_multiagent_v1 = Grok 4.20 multi-agent via xAI Responses "
             "with Apify LinkedIn/Instagram Remote MCP. "
             "critic_react_v3 = single ReAct + open-question ledger + "
             "adversarial critic; supports --preset and --goal.",
    )
```

Add (anywhere inside `_build_parser`, before `return parser`) the new flags:

```python
    s.add_argument(
        "--preset",
        choices=["coffee_career", "coffee_personal", "reconnect",
                 "sales_outreach", "dossier", "general"],
        default="general",
        help="critic_react_v3 only: canned investigation posture. "
             "Combined with --goal in the system prompt. Default: general.",
    )
    s.add_argument(
        "--goal", type=str, default="",
        help="critic_react_v3 only: free-form goal text appended after "
             "the preset preamble in the system prompt.",
    )
    s.add_argument(
        "--max-critic-rejections", type=int, default=None,
        help="critic_react_v3 only: cap on critic rejection cycles "
             "(default 3). The whole-scan budget still applies on top.",
    )
    s.add_argument(
        "--max-recursion-per-engagement", type=int, default=None,
        help="critic_react_v3 only: per-engagement LangGraph recursion "
             "limit (default 50). Caps tool calls in one agent invocation.",
    )
```

In `main()` (around line 171), wire them through:

```python
    kwargs["agent_version"] = args.agent
    kwargs["preset"] = args.preset
    kwargs["goal"] = args.goal
    if args.max_critic_rejections is not None:
        kwargs["max_critic_rejections"] = args.max_critic_rejections
    if args.max_recursion_per_engagement is not None:
        kwargs["max_recursion_per_engagement"] = args.max_recursion_per_engagement
    if args.max_processor_tool_calls is not None:
        kwargs["max_processor_tool_calls"] = args.max_processor_tool_calls
    if args.max_verifier_iterations is not None:
        kwargs["max_verifier_iterations"] = args.max_verifier_iterations
    if args.enable:
        kwargs["enabled_tools"] = set(args.enable)
```

- [ ] **Step 4: Run, verify pass**

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add osint/cli.py tests/test_cli.py
git commit -m "feat(cli): --preset / --goal / --max-critic-rejections flags for critic_react_v3"
```

---

## Task 16 — README update

**Files:**
- Modify: `README.md:5-9`

- [ ] **Step 1: Add the fourth bullet**

Edit the bullet list in `README.md` to add a fourth entry:

```markdown
- **`react_v1`** (default) — single ReAct loop with multi-pass deepen. Fast (~3-10 min), modest cost (~$0.30-0.70 per scan). Good for quick lookups.
- **`leadqueue_v2`** — priority-queue investigation with verifier loop. ~$0.30-0.50 per scan in practice. Designed for deep-dive scans where v1 returns shallow profiles.
- **`xai_multiagent_v1`** — Grok 4.20 multi-agent via xAI Responses API ...
- **`critic_react_v3`** — single ReAct loop + open-question ledger + adversarial critic. Goal-conditioned via `--preset` (coffee_career, coffee_personal, reconnect, sales_outreach, dossier, general) and free-form `--goal`. Fully general; no atom taxonomy or specialist roster. Designed to convert unused tool-call budget into recall via parallel tool emission and critic-driven re-engagement.
```

Also add an example invocation under the existing examples:

```markdown
python -m osint.cli scan "Jane Doe" --agent critic_react_v3 --preset coffee_career --goal "Meeting about her transformer-inference work"
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme): add critic_react_v3 agent and example invocation"
```

---

## Task 17 — Full test sweep

- [ ] **Step 1: Run the entire suite**

```bash
.venv/bin/python -m pytest 2>&1 | tail -30
```
Expected: all passing. If any earlier-suite tests broke (e.g. `tests/agents/leadqueue_v2/`, `tests/test_cli.py`), fix the regression in the originating task — do NOT mask it here.

- [ ] **Step 2: Confirm no `react_v1` / `leadqueue_v2` regressions**

```bash
.venv/bin/python -m pytest tests/agents/react_v1 tests/agents/leadqueue_v2 -v 2>&1 | tail -20
```
Expected: all PASS.

- [ ] **Step 3: Smoke (manual, optional)**

This is not a CI step — it's a sanity check the implementer should run before claiming done if API keys are available.

```bash
.venv/bin/python -m osint.cli scan "https://www.instagram.com/<some_test_subject>/" \
  --agent critic_react_v3 \
  --preset coffee_career \
  --goal "Quick coffee chat about their work" \
  --budget-usd 2.0 \
  --max-calls 60
```

Read the resulting JSON in `scans/`. Confirm:
- `tool_calls` count is meaningfully higher than a comparable `react_v1` run on the same subject.
- The transcript shows at least one critic round (look for the "reviewer flagged these gaps" synthetic message).
- Final report is non-empty and references the goal.

If parallel tool calls are not appearing per turn (Grok 4.20 emits singletons), this is the spec's flagged risk, not a bug — open a follow-up.

- [ ] **Step 4: Commit (only if anything beyond Task 16 was needed to land green)**

If Step 1 surfaced regressions and you fixed them in this task's files, commit the fixes. Otherwise no commit needed.

---

## Self-review checklist (run before claiming done)

- [ ] All 8 spec sections (motivation, architecture, inputs, outer loop, system prompt, ledger, critic, termination, presets, config, CLI, files, backwards compat, testing, risks) have at least one corresponding task.
- [ ] No placeholders (`TBD`, "implement later", "similar to Task N").
- [ ] No method/function name drift across tasks.
- [ ] `parse_report` is imported from `osint.agents.react_v1.prompts` (not redefined).
- [ ] `_synthesize` and `_serialize_messages` are imported from `osint.agents.react_v1.runner` (not redefined).
- [ ] `StopReason.CRITIC_ACCEPTED` and `StopReason.CRITIC_EXHAUSTED` are added to the enum and used by the runner.
- [ ] `ScanConfig.preset` Literal includes exactly the six listed values; `PRESETS` dict has exactly those six keys.
- [ ] CLI `--preset` choices and the `Literal` in `ScanConfig` agree exactly.
- [ ] `critic_react_v3` is added to `AGENTS` AND to the `--agent` CLI choices.
- [ ] `max_critic_rejections` and `max_recursion_per_engagement` flow CLI → `ScanConfig` → runner.
