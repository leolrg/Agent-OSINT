import asyncio
import json
from typing import Any, Type

import maigret as _maigret_pkg
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


# Process-wide politeness cap. See spec §6.6.
_MAIGRET_SEMAPHORE = asyncio.Semaphore(2)

# Resolve the entrypoint at import time. If the maigret release on PyPI ever
# moves it, the package must be pinned to a known-good version in pyproject.toml.
_MAIGRET_SEARCH = getattr(_maigret_pkg, "search", None)


class MaigretInput(BaseModel):
    username: str = Field(description="The username to search for.")
    max_connections: int = Field(default=15, ge=1, le=50)
    timeout: int = Field(default=10, ge=1, le=30)
    sites_filter: list[str] | None = Field(
        default=None,
        description="Restrict the check to these site names.",
    )


async def _search(
    *,
    username: str,
    max_connections: int,
    timeout: int,
    proxy: str | None,
    site_list: list[str] | None,
) -> dict:
    def _run() -> dict:
        return _MAIGRET_SEARCH(
            username=username,
            max_connections=max_connections,
            timeout=timeout,
            proxy=proxy,
            site_list=site_list,
        )

    return await asyncio.to_thread(_run)


class MaigretTool(BaseTool):
    name: str = "maigret"
    description: str = (
        "Check ~3000 websites for the presence of a username and return the "
        "sites where the account exists. Use after you have a confirmed or "
        "likely username. Pass `sites_filter` to restrict the fan-out."
    )
    args_schema: Type[BaseModel] = MaigretInput
    response_format: str = "content_and_artifact"

    proxy_url: str | None = None

    def _run(self, *args, **kwargs):
        raise NotImplementedError("Use async invocation.")

    async def _arun(
        self,
        username: str,
        max_connections: int = 15,
        timeout: int = 10,
        sites_filter: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict]:
        async with _MAIGRET_SEMAPHORE:
            raw = await _search(
                username=username,
                max_connections=max_connections,
                timeout=timeout,
                proxy=self.proxy_url,
                site_list=sites_filter,
            )

        found = [
            {"site": site, "url": info.get("url_user"), "status": info.get("status", {}).get("message")}
            for site, info in (raw or {}).items()
            if info.get("status", {}).get("message") in {"Claimed", "Found"}
        ]
        artifact = {"found_accounts": found, "raw": raw}
        content = json.dumps({"found_accounts": found}, default=str)
        return content, artifact
