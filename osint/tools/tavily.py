"""Tavily-backed web_search and web_extract tools."""

import json
import os
from typing import Any, Type

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr

from osint.errors import ScanConfigError


TAVILY_BASE_URL = "https://api.tavily.com"
_TIMEOUT_SECS = 60.0


class TavilySearchInput(BaseModel):
    query: str = Field(description="Web search query.")
    max_results: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Requested result count. Tavily returns at most 20 per call.",
    )


_TAVILY_SEARCH_DESC = (
    "Web Search via Tavily Search. Returns ranked results with URL, title, "
    "and snippet content. Tavily supports a maximum of 20 results per call; "
    "larger max_results values are capped."
)


class TavilySearchTool(BaseTool):
    name: str = "web_search"
    description: str = _TAVILY_SEARCH_DESC
    args_schema: Type[BaseModel] = TavilySearchInput
    response_format: str = "content_and_artifact"

    api_key: str | None = None
    search_depth: str = "advanced"
    _client: httpx.AsyncClient | None = PrivateAttr(default=None)

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        search_depth: str = "advanced",
        **kwargs: Any,
    ):
        super().__init__(api_key=api_key, search_depth=search_depth, **kwargs)
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=TAVILY_BASE_URL, timeout=_TIMEOUT_SECS)
        return self._client

    @property
    def auth_header(self) -> dict[str, str]:
        api_key = self.api_key or os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise ScanConfigError("TAVILY_API_KEY environment variable is not set.")
        return {"Authorization": f"Bearer {api_key}"}

    def _run(self, *args, **kwargs):
        raise NotImplementedError("Use async invocation.")

    async def _arun(
        self,
        query: str,
        max_results: int = 20,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict]:
        payload = {
            "query": query,
            "max_results": min(max_results, 20),
            "search_depth": self.search_depth,
            "include_answer": False,
            "include_raw_content": False,
            "include_usage": True,
        }
        response = await self.client.post(
            "/search",
            json=payload,
            headers=self.auth_header,
        )
        response.raise_for_status()
        raw = response.json()
        results = []
        for idx, result in enumerate(raw.get("results") or [], start=1):
            results.append(
                {
                    "url": result.get("url"),
                    "title": result.get("title"),
                    "content": result.get("content") or "",
                    "position": idx,
                    "score": result.get("score"),
                }
            )
        artifact = {"query": query, "results": results, "raw": raw}
        content = json.dumps({"query": query, "results": results}, default=str, ensure_ascii=False)
        return content, artifact


class TavilyExtractInput(BaseModel):
    urls: list[str] = Field(description="One or more URLs to fetch.")


_TAVILY_EXTRACT_DESC = (
    "Fetch one or more URLs and return extracted Markdown content via "
    "Tavily Extract."
)


class TavilyExtractTool(BaseTool):
    name: str = "web_extract"
    description: str = _TAVILY_EXTRACT_DESC
    args_schema: Type[BaseModel] = TavilyExtractInput
    response_format: str = "content_and_artifact"

    api_key: str | None = None
    extract_depth: str = "advanced"
    _client: httpx.AsyncClient | None = PrivateAttr(default=None)

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        extract_depth: str = "advanced",
        **kwargs: Any,
    ):
        super().__init__(api_key=api_key, extract_depth=extract_depth, **kwargs)
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=TAVILY_BASE_URL, timeout=_TIMEOUT_SECS)
        return self._client

    @property
    def auth_header(self) -> dict[str, str]:
        api_key = self.api_key or os.getenv("TAVILY_API_KEY")
        if not api_key:
            raise ScanConfigError("TAVILY_API_KEY environment variable is not set.")
        return {"Authorization": f"Bearer {api_key}"}

    def _run(self, *args, **kwargs):
        raise NotImplementedError("Use async invocation.")

    async def _arun(
        self,
        urls: list[str],
        run_manager: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict]:
        payload = {
            "urls": urls,
            "extract_depth": self.extract_depth,
            "format": "markdown",
            "include_images": False,
            "include_usage": True,
        }
        response = await self.client.post(
            "/extract",
            json=payload,
            headers=self.auth_header,
        )
        response.raise_for_status()
        raw = response.json()
        results = []
        for result in raw.get("results") or []:
            results.append(
                {
                    "url": result.get("url"),
                    "raw_content": result.get("raw_content") or "",
                }
            )
        artifact = {
            "results": results,
            "failed_results": raw.get("failed_results") or [],
            "raw": raw,
        }
        content = json.dumps({"results": results}, default=str, ensure_ascii=False)
        return content, artifact
