import asyncio
import json
import logging
from typing import Any, Type

import maigret as _maigret_pkg
from langchain_core.tools import BaseTool
from maigret.db_updater import BUNDLED_DB_PATH
from maigret.result import MaigretCheckStatus
from maigret.sites import MaigretDatabase
from pydantic import BaseModel, Field


# Process-wide politeness cap. See spec §6.6.
_MAIGRET_SEMAPHORE = asyncio.Semaphore(2)

# Real maigret.search (re-exported from maigret.checking.maigret) — investigated
# against installed maigret==0.6.0. Signature (verified via inspect.signature):
#
#   async def maigret(
#       username, site_dict, logger, query_notify=None, proxy=None,
#       tor_proxy=None, i2p_proxy=None, timeout=3, is_parsing_enabled=False,
#       id_type='username', debug=False, forced=False,
#       max_connections=100, no_progressbar=False, cookies=None,
#       retries=0, check_domains=False, *args, **kwargs,
#   ) -> Dict[str, Any]
#
# - It is async (`await` directly; no asyncio.to_thread).
# - There is NO `site_list` parameter; we pre-filter `site_dict` by site name.
# - Per-site result dict has a `status` key whose value is a `MaigretCheckResult`
#   object (NOT a nested dict). Use `result.status == MaigretCheckStatus.CLAIMED`
#   to detect a found account; `str(result.status)` yields "Claimed"/"Available"/etc.
#
# Direct attribute access — fail loudly at import if maigret bumps a major
# version and renames `search`. Per project policy: no defensive fallbacks for
# impossible cases.
_MAIGRET_SEARCH = _maigret_pkg.search

# Module-level shared logger. maigret needs a real logger (not Mock) when
# debug=True, but for our normal use a quiet logger is fine.
_LOGGER = logging.getLogger("osint.maigret")

# Load the bundled site DB once at import time. The DB has ~3000 sites and is
# parsed from JSON; doing it on every _search call would waste ~tens-of-ms per
# invocation. Loading at import keeps the hot path fast.
_DB: MaigretDatabase = MaigretDatabase().load_from_path(BUNDLED_DB_PATH)
_SITE_DICT: dict = _DB.sites_dict


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
    """Call the real async maigret.search with a pre-filtered site_dict."""
    if site_list:
        wanted = {name.lower() for name in site_list}
        filtered = {
            name: site
            for name, site in _SITE_DICT.items()
            if name.lower() in wanted
        }
    else:
        filtered = _SITE_DICT

    return await _MAIGRET_SEARCH(
        username=username,
        site_dict=filtered,
        logger=_LOGGER,
        timeout=timeout,
        max_connections=max_connections,
        proxy=proxy,
        no_progressbar=True,
    )


def _status_str(info: dict) -> str | None:
    """Extract the status string from a maigret per-site result dict.

    info["status"] is a MaigretCheckResult; str(result.status) yields
    "Claimed" / "Available" / "Unknown" / "Illegal".
    """
    result = info.get("status")
    if result is None:
        return None
    inner = getattr(result, "status", None)
    if isinstance(inner, MaigretCheckStatus):
        return inner.value
    return str(result) if result is not None else None


def _make_raw_jsonsafe(raw: dict | None) -> dict:
    """Strip / normalize maigret's per-site response into a JSON-clean dict.

    The raw response from `maigret.search` is `{site_name: info_dict}` where
    info_dict can carry:
      - a ``MaigretSite`` object under the ``"site"`` key (the full site
        config: regex patterns, headers, etc. — huge and useless for our
        audit log).
      - a ``MaigretCheckResult`` object under the ``"status"`` key (an
        enum + message + context).
      - other plain primitives (``url_user``, ``http_status``, ``rank``).

    Pydantic's model_dump(mode="json") on a ToolCallRecord whose raw field
    holds a MaigretSite will raise PydanticSerializationError, so we
    normalize here before handing it off. We DROP the ``site`` key
    entirely (we don't need it) and reduce ``status`` to a small dict.
    Everything else passes through if JSON-clean, otherwise becomes a
    truncated repr — preserves diagnostic value without breaking writes.
    """
    safe: dict[str, dict] = {}
    for site_name, info in (raw or {}).items():
        if not isinstance(info, dict):
            safe[str(site_name)] = {"_repr": repr(info)[:200]}
            continue
        clean: dict = {}
        for k, v in info.items():
            if k == "site":
                # MaigretSite — full site config; drop entirely.
                continue
            if k == "status" and v is not None and not isinstance(v, (str, dict)):
                # MaigretCheckResult — flatten to a small dict.
                inner = getattr(v, "status", None)
                clean[k] = {
                    "status": (inner.value if isinstance(inner, MaigretCheckStatus)
                               else str(inner) if inner is not None else None),
                    "message": str(getattr(v, "message", "") or "") or None,
                    "context": str(getattr(v, "context", "") or "") or None,
                }
                continue
            if isinstance(v, (str, int, float, bool, type(None))):
                clean[k] = v
            elif isinstance(v, (list, tuple)):
                clean[k] = [
                    x if isinstance(x, (str, int, float, bool, type(None))) else repr(x)[:200]
                    for x in v
                ]
            elif isinstance(v, dict):
                clean[k] = {str(kk): (vv if isinstance(vv, (str, int, float, bool, type(None)))
                                      else repr(vv)[:200])
                            for kk, vv in v.items()}
            else:
                clean[k] = repr(v)[:200]
        safe[str(site_name)] = clean
    return safe


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
        max_connections: int,
        timeout: int,
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

        found = []
        blocked = []
        for site, info in (raw or {}).items():
            status = _status_str(info)
            entry = {
                "site": site,
                "url": info.get("url_user"),
                "status": status,
            }
            if status == "Claimed":
                found.append(entry)
            elif status == "Unknown" and info.get("http_status") in (403, 429):
                # Surface likely WAF/rate-limit blocks in the artifact so the
                # agent can distinguish "site exists but blocked" from
                # "site doesn't have this user".
                blocked.append(entry)

        artifact = {
            "found_accounts": found,
            "blocked": blocked,
            "raw": _make_raw_jsonsafe(raw),
        }
        content = json.dumps({"found_accounts": found}, default=str)
        return content, artifact
