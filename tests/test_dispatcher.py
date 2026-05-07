"""Dispatcher routing — confirms `scan()` looks up the agent runner via
the AGENTS registry by `config.agent_version`. The runner is mocked so
this test doesn't double-cover ReactV1Runner's loop logic (already
covered by tests/test_run.py)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from osint.agents import AGENTS
from osint.agents.critic_react_v3 import CriticReactV3Runner
from osint.run import scan
from osint.types import ScanConfig


@pytest.fixture(autouse=True)
def _apify_env(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "test")


async def test_dispatcher_routes_to_react_v1(tmp_path, monkeypatch):
    """agent_version='react_v1' (default) routes to ReactV1Runner.

    Patch the registry so we can assert the lookup happened with the
    right key without exercising the real runner's tool-building / loop."""
    fake_runner_cls = MagicMock()
    fake_runner_instance = MagicMock()
    fake_runner_instance.run = AsyncMock(
        return_value=({"extracted_identifiers": {}, "report": {"text": "ok"}}, None)
    )
    fake_runner_cls.return_value = fake_runner_instance

    monkeypatch.setitem(__import__("osint.agents", fromlist=["AGENTS"]).AGENTS,
                        "react_v1", fake_runner_cls)

    cfg = ScanConfig(enabled_tools={"web_search"})
    await scan(subject="Jane", config=cfg, llm=MagicMock(), scans_dir=tmp_path)
    fake_runner_cls.assert_called_once_with()
    fake_runner_instance.run.assert_awaited_once()


async def test_dispatcher_rejects_unknown_agent_version(tmp_path):
    from osint.errors import ScanConfigError
    cfg = ScanConfig(enabled_tools={"web_search"}, agent_version="does_not_exist")
    with pytest.raises(ScanConfigError, match="unknown agent_version"):
        await scan(subject="Jane", config=cfg, llm=MagicMock(), scans_dir=tmp_path)


def test_critic_react_v3_registered():
    assert "critic_react_v3" in AGENTS
    assert AGENTS["critic_react_v3"] is CriticReactV3Runner
