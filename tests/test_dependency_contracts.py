"""Contract tests for vendor APIs we depend on. Each one is a small
inspect.signature / attribute check that fails loudly if a future package
release moves a load-bearing symbol or argument. No network."""
import inspect

from langchain_tavily import TavilyExtract, TavilySearch
from langgraph.prebuilt import create_react_agent


def test_tavily_search_name_is_tavily_search(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    assert TavilySearch().name == "tavily_search", (
        "langchain-tavily renamed TavilySearch.name; update routing rules "
        "in osint/prompts.py and the _COSTS table in osint/tools/__init__.py."
    )


def test_tavily_extract_name_is_tavily_extract(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    assert TavilyExtract().name == "tavily_extract", (
        "langchain-tavily renamed TavilyExtract.name; update routing rules."
    )


def test_create_react_agent_accepts_prompt_kwarg():
    params = inspect.signature(create_react_agent).parameters
    assert "prompt" in params, (
        "langgraph removed the `prompt=` kwarg on create_react_agent. "
        "If migrating to langgraph 2.x, switch to "
        "`from langchain.agents import create_agent` and rename the kwarg "
        "to `system_prompt=`. See osint/run.py."
    )
