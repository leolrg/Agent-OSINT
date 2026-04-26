import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from osint.run import scan
from osint.types import LLMConfig, LLMPricing, ScanConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m osint.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="Run a scan from a subject description.")
    s.add_argument("subject", nargs="?", default=None,
                   help="Free-form subject description. If omitted, read from stdin.")
    s.add_argument("--scans-dir", type=Path, default=Path("./scans"))
    s.add_argument("--budget-usd", type=float, default=5.0)
    s.add_argument("--max-calls", type=int, default=100)
    s.add_argument("--max-seconds", type=int, default=600)
    s.add_argument("--passes", type=int, default=1,
                   help="Number of agent passes per scan. Pass 1 is the "
                        "initial investigation; passes 2..N are 'deepen' "
                        "passes that critique the previous pass's draft and "
                        "use new tool calls to fill gaps. Budget/call/time "
                        "caps apply to the WHOLE scan, not per-pass — so if "
                        "you bump --passes you may also want a higher "
                        "--budget-usd / --max-calls. Default: 1.")
    s.add_argument(
        "--agent",
        choices=["react_v1", "leadqueue_v2"],
        default="react_v1",
        help="Agent runner. react_v1 = ReAct loop with multi-pass deepen "
             "(default; behaves like before this flag existed). "
             "leadqueue_v2 = priority-queue investigation with verifier loop.",
    )
    s.add_argument("--enable", action="append", default=None,
                   help="Enable a tool by name. Repeatable. Defaults to the standard free set.")
    s.add_argument("--env-file", type=Path, default=None,
                   help="Path to a .env file to load API keys from "
                        "(default: walk up from cwd looking for .env). Existing "
                        "shell environment variables are NOT overridden.")
    # LLM swap. Any OpenAI-compatible chat completions endpoint works.
    s.add_argument("--llm-model", default=None,
                   help="LLM model name. Falls back to $OSINT_LLM_MODEL, then "
                        "grok-4.20.")
    s.add_argument("--llm-base-url", default=None,
                   help="OpenAI-compatible chat-completions base URL. Falls "
                        "back to $OSINT_LLM_BASE_URL, then https://api.x.ai/v1. "
                        "Examples: https://api.openai.com/v1, "
                        "https://api.deepseek.com/v1, http://localhost:11434/v1.")
    s.add_argument("--llm-api-key-env", default=None,
                   help="Env var that holds the API key for the LLM. Falls "
                        "back to $OSINT_LLM_API_KEY_ENV, then XAI_API_KEY.")
    s.add_argument("--llm-input-mtok-usd", type=float, default=None,
                   help="Per-million-input-token cost in USD. Falls back to "
                        "$OSINT_LLM_INPUT_MTOK_USD, then grok-4.20 ($2.0).")
    s.add_argument("--llm-output-mtok-usd", type=float, default=None,
                   help="Per-million-output-token cost in USD. Falls back to "
                        "$OSINT_LLM_OUTPUT_MTOK_USD, then grok-4.20 ($6.0).")
    return parser


# Recognized env-var names for the LLM-config layer (loaded from .env or
# exported in the shell). CLI flags override these; these override LLMConfig
# defaults. See _resolve_llm_config.
_ENV_LLM_MODEL = "OSINT_LLM_MODEL"
_ENV_LLM_BASE_URL = "OSINT_LLM_BASE_URL"
_ENV_LLM_API_KEY_ENV = "OSINT_LLM_API_KEY_ENV"
_ENV_LLM_INPUT_MTOK_USD = "OSINT_LLM_INPUT_MTOK_USD"
_ENV_LLM_OUTPUT_MTOK_USD = "OSINT_LLM_OUTPUT_MTOK_USD"


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError as e:
        raise SystemExit(f"error: {name}={raw!r} is not a valid number ({e})")


def _resolve_llm_config(args) -> LLMConfig | None:
    """Build the LLMConfig by merging three layers, in precedence order:

      1. `--llm-*` CLI flags (highest)
      2. `OSINT_LLM_*` environment variables (.env-friendly defaults)
      3. `LLMConfig()` hardcoded defaults — Grok 4.20 (lowest)

    Returns None when nothing was overridden, so `ScanConfig`'s default
    `llm` field kicks in unchanged. Otherwise returns a fully-resolved
    `LLMConfig`. Must be called AFTER `load_dotenv()` so the environment
    layer is populated.
    """
    base = LLMConfig()

    env_model = os.environ.get(_ENV_LLM_MODEL) or None
    env_base_url = os.environ.get(_ENV_LLM_BASE_URL) or None
    env_api_key_env = os.environ.get(_ENV_LLM_API_KEY_ENV) or None
    env_input_rate = _env_float(_ENV_LLM_INPUT_MTOK_USD)
    env_output_rate = _env_float(_ENV_LLM_OUTPUT_MTOK_USD)

    model = args.llm_model or env_model or base.model
    base_url = args.llm_base_url or env_base_url or base.base_url
    api_key_env_var = args.llm_api_key_env or env_api_key_env or base.api_key_env_var

    input_rate = (
        args.llm_input_mtok_usd
        if args.llm_input_mtok_usd is not None
        else env_input_rate
        if env_input_rate is not None
        else base.pricing.input_per_mtok_usd
    )
    output_rate = (
        args.llm_output_mtok_usd
        if args.llm_output_mtok_usd is not None
        else env_output_rate
        if env_output_rate is not None
        else base.pricing.output_per_mtok_usd
    )

    if (
        model == base.model
        and base_url == base.base_url
        and api_key_env_var == base.api_key_env_var
        and input_rate == base.pricing.input_per_mtok_usd
        and output_rate == base.pricing.output_per_mtok_usd
    ):
        return None

    return LLMConfig(
        model=model,
        base_url=base_url,
        api_key_env_var=api_key_env_var,
        pricing=LLMPricing(
            input_per_mtok_usd=input_rate,
            output_per_mtok_usd=output_rate,
        ),
    )


def _build_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _build_parser()
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = _build_args(argv)

    # Load .env if present. CLI-only — the library (osint.scan) stays free of
    # filesystem side effects on import. override=False so an exported shell
    # env var still wins over the .env file (.env is a default, not an
    # override). If --env-file was passed, point dotenv at it explicitly;
    # otherwise it walks up from cwd looking for a .env.
    if args.env_file is not None:
        load_dotenv(dotenv_path=args.env_file, override=False)
    else:
        load_dotenv(override=False)

    subject = args.subject if args.subject is not None else sys.stdin.read()
    if not subject or not subject.strip():
        print("error: subject must be a non-empty description", file=sys.stderr)
        sys.exit(2)

    kwargs: dict = {
        "budget_usd": args.budget_usd,
        "max_tool_calls": args.max_calls,
        "max_wall_clock_sec": args.max_seconds,
        "passes": args.passes,
    }
    kwargs["agent_version"] = args.agent
    if args.enable:
        kwargs["enabled_tools"] = set(args.enable)
    llm_cfg = _resolve_llm_config(args)
    if llm_cfg is not None:
        kwargs["llm"] = llm_cfg

    result = await scan(
        subject=subject,
        config=ScanConfig(**kwargs),
        scans_dir=args.scans_dir,
    )
    print(result.path)
    if result.markdown_path is not None:
        print(result.markdown_path)
    return 0


def _entry() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    _entry()
