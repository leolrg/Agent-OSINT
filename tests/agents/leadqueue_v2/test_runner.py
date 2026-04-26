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
    """Identity-lock lead -> 1 finding -> no new leads -> synth -> verifier
    satisfied -> done."""
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
    """Verifier returns unsatisfied once -> runner processes new lead -> re-synth -> satisfied."""
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
        # 3 verifier iterations, each unsatisfied - runner caps here.
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
