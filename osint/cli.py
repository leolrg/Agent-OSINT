import argparse
import asyncio
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
    s.add_argument("--max-calls", type=int, default=30)
    s.add_argument("--max-seconds", type=int, default=600)
    s.add_argument("--enable", action="append", default=None,
                   help="Enable a tool by name. Repeatable. Defaults to the standard free set.")
    s.add_argument("--env-file", type=Path, default=None,
                   help="Path to a .env file to load API keys from "
                        "(default: walk up from cwd looking for .env). Existing "
                        "shell environment variables are NOT overridden.")
    # LLM swap. Any OpenAI-compatible chat completions endpoint works.
    s.add_argument("--llm-model", default=None,
                   help="LLM model name (default: grok-4.20).")
    s.add_argument("--llm-base-url", default=None,
                   help="OpenAI-compatible chat-completions base URL "
                        "(default: https://api.x.ai/v1). Examples: "
                        "https://api.openai.com/v1, https://api.deepseek.com/v1, "
                        "http://localhost:11434/v1.")
    s.add_argument("--llm-api-key-env", default=None,
                   help="Env var that holds the API key for the LLM "
                        "(default: XAI_API_KEY).")
    s.add_argument("--llm-input-mtok-usd", type=float, default=None,
                   help="Per-million-input-token cost in USD; defaults to grok-4.20 ($2.0).")
    s.add_argument("--llm-output-mtok-usd", type=float, default=None,
                   help="Per-million-output-token cost in USD; defaults to grok-4.20 ($6.0).")
    return parser


def _llm_config_from_args(args) -> LLMConfig | None:
    """Build an LLMConfig only if the user passed at least one --llm-* flag.

    Otherwise return None and let ScanConfig's default kick in. Pricing
    fields default to LLMPricing's defaults if either rate flag is omitted.
    """
    flags = [args.llm_model, args.llm_base_url, args.llm_api_key_env,
             args.llm_input_mtok_usd, args.llm_output_mtok_usd]
    if all(f is None for f in flags):
        return None
    base = LLMConfig()
    pricing = LLMPricing(
        input_per_mtok_usd=args.llm_input_mtok_usd if args.llm_input_mtok_usd is not None
                           else base.pricing.input_per_mtok_usd,
        output_per_mtok_usd=args.llm_output_mtok_usd if args.llm_output_mtok_usd is not None
                            else base.pricing.output_per_mtok_usd,
    )
    return LLMConfig(
        model=args.llm_model or base.model,
        base_url=args.llm_base_url or base.base_url,
        api_key_env_var=args.llm_api_key_env or base.api_key_env_var,
        pricing=pricing,
    )


async def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

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
    }
    if args.enable:
        kwargs["enabled_tools"] = set(args.enable)
    llm_cfg = _llm_config_from_args(args)
    if llm_cfg is not None:
        kwargs["llm"] = llm_cfg

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
