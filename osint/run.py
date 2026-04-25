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
    build_synthesis_prompt,
    build_system_prompt,
    format_tool_calls_for_synthesis,
    parse_report,
)
from osint.state import ScanState, StopReason
from osint.storage import new_scan_id, write_scan_json
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
) -> str:
    summary = format_tool_calls_for_synthesis(state.tool_calls)
    msgs = [
        SystemMessage(content=build_system_prompt(subject, sorted(state.config.enabled_tools))),
        HumanMessage(content=build_synthesis_prompt(stop_reason, summary)),
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

    llm = llm or _default_llm(config)
    state = ScanState(scan_id=new_scan_id(), subject=subject, config=config)
    logger.info("scan.start", scan_id=state.scan_id, enabled_tools=sorted(config.enabled_tools))

    try:
        tools = build_tools(config, state)

        # Note: `prompt=` is the current arg name in langgraph >=0.2.60.
        # Older releases accepted `state_modifier=` as a deprecated alias;
        # newer releases removed it.
        agent = create_react_agent(
            llm,
            tools,
            prompt=SystemMessage(
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
