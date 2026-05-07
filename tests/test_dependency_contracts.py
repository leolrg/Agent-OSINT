"""Contract tests for vendor APIs we depend on. Each one is a small
inspect.signature / attribute check that fails loudly if a future package
release moves a load-bearing symbol or argument. No network."""
import inspect

from langgraph.prebuilt import create_react_agent


def test_create_react_agent_accepts_prompt_kwarg():
    params = inspect.signature(create_react_agent).parameters
    assert "prompt" in params, (
        "langgraph removed the `prompt=` kwarg on create_react_agent. "
        "If migrating to langgraph 2.x, switch to "
        "`from langchain.agents import create_agent` and rename the kwarg "
        "to `system_prompt=`. See osint/run.py."
    )
