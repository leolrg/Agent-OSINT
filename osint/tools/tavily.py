import os

from langchain_tavily import TavilyExtract, TavilySearch

from osint.errors import ScanConfigError


def _require_key() -> str:
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise ScanConfigError("TAVILY_API_KEY is not set")
    return key


def make_tavily_search(max_results: int = 10) -> TavilySearch:
    """Return langchain-tavily's TavilySearch tool, named `tavily_search`."""
    _require_key()
    return TavilySearch(max_results=max_results)


def make_tavily_extract() -> TavilyExtract:
    """Return langchain-tavily's TavilyExtract tool, named `tavily_extract`."""
    _require_key()
    return TavilyExtract()
