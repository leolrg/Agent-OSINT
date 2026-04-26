import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from osint.cli import main


# Tests run from the repo root, where the developer's real .env lives. The
# CLI's main() calls `load_dotenv()` which would read that file into
# os.environ — leaking any OSINT_LLM_* vars set in the dev's .env into
# tests asserting "Grok defaults when no flag passed".
#
# Two-part isolation:
#  1. Strip any OSINT_LLM_* vars already in the test process's environment.
#  2. Stub `osint.cli.load_dotenv` so it's a no-op unless an explicit
#     `dotenv_path=` was passed (i.e. via the --env-file flag). The two
#     tests that exercise the .env-loading path do pass --env-file, so
#     they still get real dotenv behavior; everyone else gets a clean env.
@pytest.fixture(autouse=True)
def _isolate_dotenv(monkeypatch):
    for var in (
        "OSINT_LLM_MODEL",
        "OSINT_LLM_BASE_URL",
        "OSINT_LLM_API_KEY_ENV",
        "OSINT_LLM_INPUT_MTOK_USD",
        "OSINT_LLM_OUTPUT_MTOK_USD",
    ):
        monkeypatch.delenv(var, raising=False)

    from dotenv import load_dotenv as _real_load_dotenv

    def _gated_load_dotenv(*args, **kwargs):
        # Only call through if --env-file was passed (sets dotenv_path).
        if kwargs.get("dotenv_path") is not None or args:
            return _real_load_dotenv(*args, **kwargs)
        return False

    monkeypatch.setattr("osint.cli.load_dotenv", _gated_load_dotenv)


async def test_cli_passes_subject_to_scan(tmp_path: Path):
    fake = type("R", (), {})()
    fake.scan_id = "sid"
    fake.path = tmp_path / "sid.json"
    fake.markdown_path = tmp_path / "sid.md"
    (tmp_path / "sid.json").write_text("{}")
    with patch("osint.cli.scan", new=AsyncMock(return_value=fake)) as m:
        await main(["scan", "Jane Doe, jane@e, @jdoe", "--scans-dir", str(tmp_path)])
    kwargs = m.call_args.kwargs
    assert kwargs["subject"] == "Jane Doe, jane@e, @jdoe"
    assert kwargs["scans_dir"] == tmp_path


async def test_cli_reads_stdin_when_no_arg(tmp_path: Path, monkeypatch):
    fake = type("R", (), {})()
    fake.scan_id = "sid"
    fake.path = tmp_path / "sid.json"
    fake.markdown_path = tmp_path / "sid.md"
    (tmp_path / "sid.json").write_text("{}")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("Jane from stdin"))
    with patch("osint.cli.scan", new=AsyncMock(return_value=fake)) as m:
        await main(["scan", "--scans-dir", str(tmp_path)])
    assert m.call_args.kwargs["subject"] == "Jane from stdin"


async def test_cli_exits_nonzero_on_empty_subject(tmp_path: Path):
    with pytest.raises(SystemExit) as exc:
        await main(["scan", "  ", "--scans-dir", str(tmp_path)])
    assert exc.value.code != 0


async def test_cli_swaps_llm_via_flags(tmp_path: Path):
    fake = type("R", (), {})()
    fake.scan_id = "sid"
    fake.path = tmp_path / "sid.json"
    fake.markdown_path = tmp_path / "sid.md"
    (tmp_path / "sid.json").write_text("{}")
    with patch("osint.cli.scan", new=AsyncMock(return_value=fake)) as m:
        await main([
            "scan", "Jane",
            "--scans-dir", str(tmp_path),
            "--llm-model", "gpt-5",
            "--llm-base-url", "https://api.openai.com/v1",
            "--llm-api-key-env", "OPENAI_API_KEY",
            "--llm-input-mtok-usd", "2.5",
            "--llm-output-mtok-usd", "10.0",
        ])
    cfg = m.call_args.kwargs["config"]
    assert cfg.llm.model == "gpt-5"
    assert cfg.llm.base_url == "https://api.openai.com/v1"
    assert cfg.llm.api_key_env_var == "OPENAI_API_KEY"
    assert cfg.llm.pricing.input_per_mtok_usd == 2.5
    assert cfg.llm.pricing.output_per_mtok_usd == 10.0


async def test_cli_keeps_default_llm_when_no_flags(tmp_path: Path):
    fake = type("R", (), {})()
    fake.scan_id = "sid"
    fake.path = tmp_path / "sid.json"
    fake.markdown_path = tmp_path / "sid.md"
    (tmp_path / "sid.json").write_text("{}")
    with patch("osint.cli.scan", new=AsyncMock(return_value=fake)) as m:
        await main(["scan", "Jane", "--scans-dir", str(tmp_path)])
    cfg = m.call_args.kwargs["config"]
    assert cfg.llm.model == "grok-4.20"
    assert cfg.llm.api_key_env_var == "XAI_API_KEY"


async def test_cli_loads_env_file_when_flag_passed(tmp_path: Path, monkeypatch):
    """--env-file populates os.environ from a .env file before scan() runs."""
    monkeypatch.delenv("OSINT_TEST_KEY", raising=False)
    env_file = tmp_path / "myenv"
    env_file.write_text("OSINT_TEST_KEY=loaded-from-dotenv\n")

    import os as _os
    fake = type("R", (), {})()
    fake.scan_id = "sid"
    fake.path = tmp_path / "sid.json"
    fake.markdown_path = tmp_path / "sid.md"
    (tmp_path / "sid.json").write_text("{}")
    with patch("osint.cli.scan", new=AsyncMock(return_value=fake)):
        await main(["scan", "Jane", "--scans-dir", str(tmp_path),
                    "--env-file", str(env_file)])
    assert _os.environ.get("OSINT_TEST_KEY") == "loaded-from-dotenv"


async def test_cli_env_file_does_not_override_shell_env(tmp_path: Path, monkeypatch):
    """An exported shell var wins over the .env file (override=False)."""
    monkeypatch.setenv("OSINT_TEST_KEY", "from-shell")
    env_file = tmp_path / "myenv"
    env_file.write_text("OSINT_TEST_KEY=from-dotenv\n")

    import os as _os
    fake = type("R", (), {})()
    fake.scan_id = "sid"
    fake.path = tmp_path / "sid.json"
    fake.markdown_path = tmp_path / "sid.md"
    (tmp_path / "sid.json").write_text("{}")
    with patch("osint.cli.scan", new=AsyncMock(return_value=fake)):
        await main(["scan", "Jane", "--scans-dir", str(tmp_path),
                    "--env-file", str(env_file)])
    assert _os.environ.get("OSINT_TEST_KEY") == "from-shell"


async def test_osint_llm_env_vars_set_default_llm(tmp_path: Path, monkeypatch):
    """OSINT_LLM_* env vars resolve into the LLMConfig when no CLI flag is passed."""
    monkeypatch.setenv("OSINT_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("OSINT_LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("OSINT_LLM_API_KEY_ENV", "DEEPSEEK_API_KEY")
    monkeypatch.setenv("OSINT_LLM_INPUT_MTOK_USD", "0.27")
    monkeypatch.setenv("OSINT_LLM_OUTPUT_MTOK_USD", "1.10")

    fake = type("R", (), {})()
    fake.scan_id = "sid"
    fake.path = tmp_path / "sid.json"
    fake.markdown_path = tmp_path / "sid.md"
    (tmp_path / "sid.json").write_text("{}")
    with patch("osint.cli.scan", new=AsyncMock(return_value=fake)) as m:
        await main(["scan", "Jane", "--scans-dir", str(tmp_path)])

    cfg = m.call_args.kwargs["config"]
    assert cfg.llm.model == "deepseek-chat"
    assert cfg.llm.base_url == "https://api.deepseek.com/v1"
    assert cfg.llm.api_key_env_var == "DEEPSEEK_API_KEY"
    assert cfg.llm.pricing.input_per_mtok_usd == 0.27
    assert cfg.llm.pricing.output_per_mtok_usd == 1.10


async def test_cli_flag_overrides_osint_llm_env(tmp_path: Path, monkeypatch):
    """CLI --llm-* flags win over OSINT_LLM_* env vars."""
    monkeypatch.setenv("OSINT_LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("OSINT_LLM_API_KEY_ENV", "DEEPSEEK_API_KEY")

    fake = type("R", (), {})()
    fake.scan_id = "sid"
    fake.path = tmp_path / "sid.json"
    fake.markdown_path = tmp_path / "sid.md"
    (tmp_path / "sid.json").write_text("{}")
    with patch("osint.cli.scan", new=AsyncMock(return_value=fake)) as m:
        await main([
            "scan", "Jane", "--scans-dir", str(tmp_path),
            "--llm-model", "gpt-5",
            "--llm-api-key-env", "OPENAI_API_KEY",
        ])

    cfg = m.call_args.kwargs["config"]
    assert cfg.llm.model == "gpt-5"               # CLI wins
    assert cfg.llm.api_key_env_var == "OPENAI_API_KEY"  # CLI wins


async def test_partial_osint_llm_env_keeps_other_defaults(tmp_path: Path, monkeypatch):
    """Setting just one OSINT_LLM_* var leaves the others at LLMConfig defaults."""
    monkeypatch.setenv("OSINT_LLM_MODEL", "grok-4.21")
    # Don't touch base_url / api_key_env / pricing

    fake = type("R", (), {})()
    fake.scan_id = "sid"
    fake.path = tmp_path / "sid.json"
    fake.markdown_path = tmp_path / "sid.md"
    (tmp_path / "sid.json").write_text("{}")
    with patch("osint.cli.scan", new=AsyncMock(return_value=fake)) as m:
        await main(["scan", "Jane", "--scans-dir", str(tmp_path)])

    cfg = m.call_args.kwargs["config"]
    assert cfg.llm.model == "grok-4.21"             # from env
    assert cfg.llm.base_url == "https://api.x.ai/v1"   # default
    assert cfg.llm.api_key_env_var == "XAI_API_KEY"    # default
    assert cfg.llm.pricing.input_per_mtok_usd == 2.0   # default
    assert cfg.llm.pricing.output_per_mtok_usd == 6.0  # default


async def test_invalid_osint_llm_rate_env_var_errors_clearly(tmp_path: Path, monkeypatch):
    """Bad numeric in OSINT_LLM_INPUT_MTOK_USD must fail with a clear error."""
    monkeypatch.setenv("OSINT_LLM_INPUT_MTOK_USD", "not-a-number")
    with pytest.raises(SystemExit) as exc:
        await main(["scan", "Jane", "--scans-dir", str(tmp_path)])
    assert "OSINT_LLM_INPUT_MTOK_USD" in str(exc.value)
