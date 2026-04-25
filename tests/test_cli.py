import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from osint.cli import main


async def test_cli_passes_subject_to_scan(tmp_path: Path):
    fake = type("R", (), {})()
    fake.scan_id = "sid"
    fake.path = tmp_path / "sid.json"
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
    (tmp_path / "sid.json").write_text("{}")
    with patch("osint.cli.scan", new=AsyncMock(return_value=fake)):
        await main(["scan", "Jane", "--scans-dir", str(tmp_path),
                    "--env-file", str(env_file)])
    assert _os.environ.get("OSINT_TEST_KEY") == "from-shell"
