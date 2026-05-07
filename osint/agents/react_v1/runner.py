# API notes (verified 2026-04-23 against langgraph==1.1.9 / langchain-core):
#
# create_react_agent (from langgraph.prebuilt) signature:
#   create_react_agent(model, tools, *, prompt=None, response_format=None,
#                      pre_model_hook=None, post_model_hook=None,
#                      state_schema=None, context_schema=None,
#                      checkpointer=None, store=None, interrupt_before=None,
#                      interrupt_after=None, debug=False, version='v2',
#                      name=None, **deprecated_kwargs)
#
# Key findings:
#   - `prompt=` is the correct kwarg (NOT `state_modifier=`, which was removed)
#   - agent.ainvoke takes {"messages": [...]} as input, config dict as second arg
#   - GraphRecursionError lives at langgraph.errors.GraphRecursionError (confirmed)
#   - `prompt` accepts SystemMessage | str | Callable | Runnable | None
#
# LangGraph v1.0 deprecation note:
#   `langgraph.prebuilt.create_react_agent` is deprecated in LangGraph V1.0
#   in favour of `langchain.agents.create_agent` (with `system_prompt=`).
#   We keep the import aliased as `create_react_agent` so that tests can
#   monkeypatch `osint.agents.react_v1.runner.create_react_agent` without
#   change. Migration to `create_agent` / `system_prompt=` is a one-line
#   change when the old symbol is removed.

import asyncio
from datetime import datetime, timezone
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from osint.agents.react_v1.prompts import (
    build_deepen_prompt,
    build_synthesis_prompt,
    build_system_prompt,
    format_tool_calls_for_synthesis,
    parse_report,
)
from osint.errors import ScanStopped
from osint.llm_cost import LLMCostCallback
from osint.log import logger
from osint.state import ScanState, StopReason
from osint.types import ScanConfig


async def _synthesize(
    llm: BaseChatModel,
    subject: str,
    state: ScanState,
    stop_reason: str,
    cost_cb: LLMCostCallback,
) -> tuple[str, list[Any]]:
    """Run the cap-cut synthesis fallback.

    Returns (text, [system_message, human_message, response_message]) so
    the caller can append the synthesis exchange to the scan's full
    message history alongside the agent-loop messages.
    """
    summary = format_tool_calls_for_synthesis(state.tool_calls)
    msgs = [
        SystemMessage(content=build_system_prompt(subject, sorted(state.config.enabled_tools))),
        HumanMessage(content=build_synthesis_prompt(stop_reason, summary)),
    ]
    result = await llm.ainvoke(msgs, config={"callbacks": [cost_cb]})
    return result.content or "", [*msgs, result]


def _extract_final_text(agent_result: dict) -> str:
    """Pull the last AI message's content string from a LangGraph agent result."""
    messages = agent_result.get("messages", [])
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            return m.content or ""
    return ""


def _serialize_messages(messages: list) -> list[dict]:
    """Convert a list of LangChain BaseMessages into JSON-clean dicts.

    Uses Pydantic's `model_dump(mode="json")` so timestamps, enum types,
    and nested LangChain objects (like tool_calls on AIMessage) all
    serialize correctly into the scan JSON. Falls back to a stringified
    `repr` only if the object isn't a Pydantic model — defensive against
    a future LangChain refactor.
    """
    out: list[dict] = []
    for m in messages or []:
        try:
            out.append(m.model_dump(mode="json"))
        except Exception:
            out.append({"type": type(m).__name__, "repr": repr(m)})
    return out


def _merge_identifiers(prev: dict, new: dict) -> dict:
    """Union-merge identifier dicts across passes.

    Each value is expected to be a list (emails, usernames, urls, etc.);
    we keep order and deduplicate. Non-list values fall through to
    "latest wins". Used to combine pass N's extracted_identifiers into
    the running scan-state record so a later pass cannot ACCIDENTALLY
    drop something an earlier pass found by simply forgetting to repeat
    it in its JSON tail.
    """
    merged: dict = {}
    for key in set(prev.keys()) | set(new.keys()):
        prev_v = prev.get(key)
        new_v = new.get(key)
        if isinstance(prev_v, list) and isinstance(new_v, list):
            seen: list = []
            for item in prev_v + new_v:
                if item not in seen:
                    seen.append(item)
            merged[key] = seen
        elif isinstance(prev_v, list):
            merged[key] = list(prev_v)
        elif isinstance(new_v, list):
            merged[key] = list(new_v)
        else:
            # Both non-list (rare). Latest wins.
            merged[key] = new_v if new_v is not None else prev_v
    return merged


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
    """Run one agent pass. Returns (parsed_report_dict, stop_reason_or_None).

    Pass 1 uses the standard system prompt + a "Begin the scan." seed.
    Passes 2..N use the deepen prompt that embeds the previous pass's
    report as context and instructs the agent to find gaps and extend.

    The shared `state` accumulates messages and tool calls across passes
    — there's only ONE scan budget, regardless of how many passes run.
    """
    is_first_pass = pass_num == 1

    if is_first_pass:
        system_text = build_system_prompt(subject, sorted(config.enabled_tools))
        seed_message = HumanMessage(content="Begin the scan.")
    else:
        # Summarize every tool call made in any prior pass into a one-line-
        # per-call list — same formatter we use for cap-cut synthesis.
        # The agent uses it to avoid retreading the same searches.
        prev_tool_calls = format_tool_calls_for_synthesis(state.tool_calls)
        system_text = build_deepen_prompt(
            subject=subject,
            tool_names=sorted(config.enabled_tools),
            previous_report_text=previous_report_text or "",
            previous_tool_calls_summary=prev_tool_calls,
            pass_num=pass_num,
            total_passes=total_passes,
        )
        seed_message = HumanMessage(
            content=(
                f"Begin pass {pass_num} of {total_passes}. Critique the draft "
                f"above, identify gaps and unfollowed leads, and consult the "
                f"prior-pass tool-call list to know what's been tried — then "
                f"run NEW tool calls (different queries, different URLs, "
                f"different angles) to fill the gaps. Produce v{pass_num} "
                f"of the report."
            )
        )

    agent = create_react_agent(
        llm,
        tools,
        prompt=SystemMessage(content=system_text),
    )

    initial_state = {"messages": [seed_message]}
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

    if agent_result is not None:
        state.messages.extend(_serialize_messages(agent_result.get("messages", [])))

    if stop_reason is None and agent_result is not None:
        final_text = _extract_final_text(agent_result)
        if final_text.strip():
            return parse_report(final_text), None
        # Grok-4.20 reasoning mode occasionally finishes with
        # finish_reason="stop" but completion_tokens=0 — burns reasoning,
        # emits nothing. Falling through to the synthesis path lets the
        # scan still produce a report from the tool log instead of
        # storing an empty report.
        stop_reason = StopReason.EMPTY_FINAL

    # Cap-cut path: synthesize from what we have and return.
    logger.info(
        "scan.pass.synthesize",
        scan_id=state.scan_id,
        pass_num=pass_num,
        stop_reason=stop_reason.value if stop_reason else "unknown",
    )
    synth_text, synth_msgs = await _synthesize(
        llm, subject, state, stop_reason.value if stop_reason else "unknown", cost_cb,
    )
    state.messages.extend(_serialize_messages(synth_msgs))
    return parse_report(synth_text), stop_reason


class ReactV1Runner:
    """v1 agent: single LangGraph create_react_agent loop, multi-pass deepen.

    Implements osint.agents.base.AgentRunner. The runner only owns the
    agent loop — scan-id generation, ScanState construction, tool
    building, cost-callback construction, and persistence are all
    handled by the dispatcher in osint/run.py.
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
        """Run the multi-pass v1 agent loop.

        Returns the LAST pass's parsed report dict and that pass's
        stop_reason (or None on a clean finish). Side effects: mutates
        `state` — appends messages, records the latest report,
        union-merges identifiers, and appends per-pass audit entries to
        `state.pass_reports`.
        """
        config = state.config

        # Multi-pass loop. Pass 1 = initial investigation (standard system
        # prompt + "Begin the scan." seed). Passes 2..N = "deepen" passes
        # that receive the previous pass's report as context and try to
        # fill gaps. Budget/call/time caps apply to the whole scan, not
        # per-pass — once any pass cap-cuts, we stop and finalize.
        previous_report_text: str | None = None
        last_parsed: dict = {}
        last_stop_reason: StopReason | None = None
        for pass_num in range(1, config.passes + 1):
            # Skip remaining passes if a cap was already hit by the
            # previous pass (which is reflected in state.should_stop()).
            stopped, reason = state.should_stop()
            if stopped:
                logger.info(
                    "scan.pass.skipped",
                    scan_id=state.scan_id,
                    pass_num=pass_num,
                    reason=reason.value,
                )
                break

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

            # Merge identifiers across passes so a later pass that forgot
            # to repeat a known identifier doesn't drop it from the final
            # report. Latest report TEXT wins (it's by design a superset
            # of the previous one when the deepen prompt is followed).
            merged_identifiers = _merge_identifiers(
                state.extracted_identifiers,
                parsed.get("extracted_identifiers") or {},
            )
            state.record_final_report(
                parsed.get("report") or {},
                identifiers=merged_identifiers,
            )
            previous_report_text = (parsed.get("report") or {}).get("text") or ""

            # Per-pass audit trail: record what THIS pass produced (its own
            # report, its own emitted identifiers, whether it cap-cut,
            # when it finished). Distinct from state.report (latest wins)
            # and state.extracted_identifiers (union-merged) — pass_reports
            # is the historical chain showing how the report evolved.
            state.pass_reports.append({
                "pass_num": pass_num,
                "report": parsed.get("report") or {},
                "extracted_identifiers": parsed.get("extracted_identifiers") or {},
                "stop_reason": pass_stop_reason.value if pass_stop_reason else None,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })

            logger.info(
                "scan.pass.done",
                scan_id=state.scan_id,
                pass_num=pass_num,
                cap_cut=pass_stop_reason.value if pass_stop_reason else None,
                cumulative_tool_calls=len(state.tool_calls),
                cumulative_cost_usd=state.total_cost_usd,
            )

            last_parsed = parsed
            last_stop_reason = pass_stop_reason

            # If THIS pass cap-cut, don't start a new pass — we're already
            # at budget/time/recursion limit.
            if pass_stop_reason is not None:
                break

        return last_parsed, last_stop_reason
