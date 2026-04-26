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
#   monkeypatch `osint.run.create_react_agent` without change.
#   Migration to `create_agent` / `system_prompt=` is a one-line change
#   when the old symbol is removed.

import asyncio
import os
from datetime import datetime, timezone
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
from osint.prompts import (
    build_deepen_prompt,
    build_synthesis_prompt,
    build_system_prompt,
    format_tool_calls_for_synthesis,
    parse_report,
)
from osint.state import ScanState, StopReason
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
        return parse_report(final_text), None

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
    )

    try:
        tools = build_tools(config, state)
        cost_cb = LLMCostCallback(state)

        # Multi-pass loop. Pass 1 = initial investigation (standard system
        # prompt + "Begin the scan." seed). Passes 2..N = "deepen" passes
        # that receive the previous pass's report as context and try to
        # fill gaps. Budget/call/time caps apply to the whole scan, not
        # per-pass — once any pass cap-cuts, we stop and finalize.
        previous_report_text: str | None = None
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

            # If THIS pass cap-cut, don't start a new pass — we're already
            # at budget/time/recursion limit.
            if pass_stop_reason is not None:
                break

        path = await write_scan_json(scans_dir, state, status="done")
        # Companion human-readable render. JSON stays the source of truth;
        # if the markdown write fails for any reason, log it but don't fail
        # the scan — the JSON is enough to reconstruct everything later.
        markdown_path: Path | None = None
        try:
            markdown_path = await write_scan_markdown(scans_dir, state, status="done")
        except Exception as md_err:
            logger.warning(
                "scan.markdown_write_failed",
                scan_id=state.scan_id,
                error=repr(md_err),
            )
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
            markdown_path=markdown_path,
        )
    except Exception:
        # Best-effort: persist whatever state we have so the failure is
        # auditable. If THIS write also fails, log the secondary error and
        # let the original exception propagate (do not mask it with the
        # secondary one — the original is what the caller needs to see).
        try:
            await write_scan_json(scans_dir, state, status="failed")
            await write_scan_markdown(scans_dir, state, status="failed")
        except Exception as secondary:
            logger.error(
                "scan.failed_write_failed",
                scan_id=state.scan_id,
                secondary_error=repr(secondary),
            )
        raise
