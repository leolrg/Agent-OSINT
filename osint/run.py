"""Scan dispatcher.

Routes to an agent runner (selected by `ScanConfig.agent_version`)
and owns everything around the agent loop: scan-id generation,
ScanState construction, tool building, the cost callback, and writing
the scan JSON/Markdown artifacts (success or failure path).

The actual agent loop lives in `osint.agents.<version>.runner`.
"""
import os
from pathlib import Path

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
    )

    try:
        tools = build_tools(config, state)
        cost_cb = LLMCostCallback(state)

        # Look up the agent runner. `agent_version` is added by Task B1
        # with default "react_v1"; until then, fall back to that default
        # so this dispatcher works against the current ScanConfig too.
        agent_version = getattr(config, "agent_version", "react_v1")
        if agent_version not in AGENTS:
            raise ScanConfigError(
                f"unknown agent_version: {agent_version!r}; "
                f"known: {sorted(AGENTS)}"
            )
        runner = AGENTS[agent_version]()
        await runner.run(
            subject=subject,
            state=state,
            llm=llm,
            tools=tools,
            cost_cb=cost_cb,
        )

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
