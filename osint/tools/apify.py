"""Apify-backed tools — Web Search, Web Extract, Instagram, LinkedIn, Twitter.

Verified against installed `apify-client==2.5.0`. Findings (via inspect):

- `ApifyClientAsync.actor(actor_id).call(run_input=..., ...)` is an async
  coroutine. Signature confirmed via inspect.signature:
    (*, run_input: Any = None, content_type: str | None = None,
     build: str | None = None, max_items: int | None = None,
     max_total_charge_usd: Decimal | None = None,
     restart_on_error: bool | None = None,
     memory_mbytes: int | None = None, timeout_secs: int | None = None,
     webhooks: list[dict] | None = None,
     force_permission_level: ActorPermissionLevel | None = None,
     wait_secs: int | None = None,
     logger: Logger | None | Literal['default'] = 'default') -> dict | None

- The returned dict is the raw Apify REST API JSON for the run object.
  `apify-client` does NOT convert keys to snake_case; the dataset id is keyed
  `defaultDatasetId` (camelCase), matching the Apify API spec. Verified by
  reading the source: `actor.call` ultimately returns the result of
  `RunClient.wait_for_finish` whose body is the raw API JSON without any case
  transformation.

- `ApifyClientAsync.dataset(dataset_id).list_items(...)` is an async coroutine
  returning a `ListPage` object (defined in apify_client/_types.py). The page
  exposes `.items: list[T]`, `.count`, `.offset`, `.limit`, `.total`, `.desc`.
  We use `.items`.

Direct attribute access — fail loudly if apify-client bumps a major version
and renames these methods. Per project policy: no defensive fallbacks.
"""

import json
import os
from typing import Any, Type

from apify_client import ApifyClientAsync
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr

from osint.errors import ScanConfigError


# All three slugs verified live against api.apify.com/v2/acts/<slug> (2026-04).
# Apify (the company) does NOT publish a LinkedIn profile scraper — that's
# why we point at the dev_fusion community actor, which is the most-active
# maintained one ($10 / 1k profiles).
DEFAULT_GOOGLE_SEARCH_ACTOR = "apify~google-search-scraper"
DEFAULT_WEB_CRAWLER_ACTOR = "apify~website-content-crawler"
DEFAULT_IG_ACTOR = "apify~instagram-scraper"
DEFAULT_LI_ACTOR = "dev_fusion~linkedin-profile-scraper"
DEFAULT_TW_ACTOR = "gentle_cloud~twitter-tweets-scraper"


# Per-actor wall-clock cap (seconds) passed to actor.call(timeout_secs=...).
# Apify enforces this server-side: when the run hits the cap it transitions
# to status="TIMED-OUT" and any partial dataset items collected so far are
# still readable. Without an explicit cap, an actor can sit in retry loops
# for up to its build-default timeout (often 1 hour) and block the agent
# turn that's awaiting it. Empirically (2026-04-26), `gentle_cloud/twitter-
# tweets-scraper` will burn the full 60 minutes whenever its shared X
# cookie pool gets 429-rate-limited — observed twice in one scan.
_DEFAULT_TIMEOUT_SECS = 180
_ACTOR_TIMEOUT_SECS: dict[str, int] = {
    # Google SERP fetch — even with retry, finishes fast (avg ~12s observed).
    DEFAULT_GOOGLE_SEARCH_ACTOR: 60,
    # HTTP-only cheerio crawl. Most pages are <30s; allow generous slack
    # for sites that 5xx-retry. Anything past 3 min is dead, move on.
    DEFAULT_WEB_CRAWLER_ACTOR: 180,
    # IG profile-by-URL is very fast (avg ~5s).
    DEFAULT_IG_ACTOR: 60,
    # LinkedIn profile scrape includes proxy rotation; ~25s typical.
    DEFAULT_LI_ACTOR: 120,
    # Twitter actor's cookie pool routinely 429s, causing the actor to
    # retry indefinitely inside its 1-hour cap. Bound it tightly so the
    # agent isn't blocked on Twitter for 60 min when the pool is dry.
    DEFAULT_TW_ACTOR: 120,
}


async def _run_actor(
    client: ApifyClientAsync,
    actor_id: str,
    run_input: dict,
) -> dict:
    """Invoke an Apify actor and pull dataset items.

    Returns a dict shaped {"items": [...], "raw": {"default_dataset_id": ...,
    "items": [...]}} where the raw block is what we persist to the scan log.

    Passes a per-actor `timeout_secs` so a stuck actor (e.g. the Twitter
    scraper when its cookie pool is exhausted) can't block the scan for
    its full default run-timeout. On TIMED-OUT we still read whatever
    partial dataset items the actor managed to push before being killed —
    that's frequently a usable subset rather than nothing.

    Wraps the actor.call() in a thin try/except that re-raises with the
    actor_id embedded in the message, since apify-client's default error
    ("Actor with this name was not found") doesn't say which slug failed —
    making it impossible to diagnose without a stack trace inspection.
    """
    timeout = _ACTOR_TIMEOUT_SECS.get(actor_id, _DEFAULT_TIMEOUT_SECS)
    try:
        run = await client.actor(actor_id).call(run_input=run_input, timeout_secs=timeout)
    except Exception as e:
        # Re-raise with the actor_id in the message but preserve the original
        # exception via __cause__ for the LangChain tool-error handler.
        # Avoid type(e)(...) — ApifyApiError has a multi-arg __init__ that
        # rejects a single-string call.
        raise RuntimeError(
            f"Apify actor call failed for actor_id={actor_id!r}: "
            f"{type(e).__name__}: {e}"
        ) from e
    if not run:
        return {"items": [], "raw": {"default_dataset_id": None, "items": []}}
    dataset_id = run["defaultDatasetId"]
    page = await client.dataset(dataset_id).list_items()
    items = page.items
    return {
        "items": items,
        "raw": {"default_dataset_id": dataset_id, "items": items},
    }


# ---------------------------------------------------------------------------
# Web search — apify/google-search-scraper
# ---------------------------------------------------------------------------


class WebSearchInput(BaseModel):
    query: str = Field(description="Web search query (Google Search syntax).")
    max_results: int = Field(
        default=30, ge=1, le=100,
        description="Up to ~100 organic results across multiple SERP pages.",
    )


_WEB_SEARCH_DESC = (
    "Google Web Search via apify/google-search-scraper. Returns ranked "
    "organic results with URL, title, and a 100–250 char snippet per "
    "result. Read every snippet word-for-word — handles, emails, and "
    "project names commonly leak inline.\n\n"
    "Args:\n"
    "  query: Google search syntax. Supports OR groups, quoted phrases, "
    "site:, intitle:, intext:, filetype:.\n"
    "  max_results: 1–100, default 30. Internally requests "
    "ceil(max_results/10) SERP pages — Google returns 10/page; the actor's "
    "resultsPerPage parameter is ignored as of 2026-04 (verified live).\n\n"
    "Returns: {results: [{url, title, content, position}, ...]}."
)


class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = _WEB_SEARCH_DESC
    args_schema: Type[BaseModel] = WebSearchInput
    response_format: str = "content_and_artifact"

    actor_id: str = DEFAULT_GOOGLE_SEARCH_ACTOR
    _client: ApifyClientAsync | None = PrivateAttr(default=None)

    def __init__(
        self,
        client: ApifyClientAsync | None = None,
        actor_id: str = DEFAULT_GOOGLE_SEARCH_ACTOR,
        **kwargs: Any,
    ):
        super().__init__(actor_id=actor_id, **kwargs)
        self._client = client

    @property
    def client(self) -> ApifyClientAsync:
        if self._client is None:
            self._client = _build_client()
        return self._client

    def _run(self, *args, **kwargs):
        raise NotImplementedError("Use async invocation.")

    async def _arun(
        self,
        query: str,
        max_results: int = 30,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict]:
        # Google returns 10 per page; actor's resultsPerPage is ignored. To
        # get N results, request ceil(N/10) pages. Each page costs $0.0018.
        pages = max(1, (max_results + 9) // 10)
        run_input = {
            "queries": query,
            "maxPagesPerQuery": pages,
            "saveHtml": False,
            "saveHtmlToKeyValueStore": False,
        }
        result = await _run_actor(self.client, self.actor_id, run_input)
        # Each dataset item is one SERP page. Flatten organic results across
        # pages, preserving rank, capped at max_results.
        organic: list[dict] = []
        for page in result["items"]:
            for r in (page.get("organicResults") or []):
                organic.append({
                    "url": r.get("url"),
                    "title": r.get("title"),
                    "content": r.get("description") or "",
                    "position": r.get("position"),
                })
            if len(organic) >= max_results:
                break
        organic = organic[:max_results]
        artifact = {"query": query, "results": organic, "raw": result["raw"]}
        content = json.dumps({"query": query, "results": organic}, default=str, ensure_ascii=False)
        return content, artifact


# ---------------------------------------------------------------------------
# Web extract — apify/website-content-crawler
# ---------------------------------------------------------------------------


class WebExtractInput(BaseModel):
    urls: list[str] = Field(description="One or more URLs to fetch.")


_WEB_EXTRACT_DESC = (
    "Fetch one or more URLs and return their text content as Markdown via "
    "apify/website-content-crawler. Use after web_search on URLs that look "
    "promising — the search snippet alone is often too short.\n\n"
    "Args:\n"
    "  urls: list of full https:// URLs. One actor run handles all URLs.\n\n"
    "Cannot extract login-walled / scraper-blocked origins (returns empty "
    "or 403): linkedin.com, instagram.com, facebook.com, tiktok.com, "
    "x.com, twitter.com, threads.net, zhihu.com, weibo.com. For LinkedIn "
    "route to apify_linkedin; Instagram → apify_instagram; X/Twitter → "
    "apify_twitter. For Zhihu/Weibo, the search snippet from web_search "
    "is your best evidence."
)


class WebExtractTool(BaseTool):
    name: str = "web_extract"
    description: str = _WEB_EXTRACT_DESC
    args_schema: Type[BaseModel] = WebExtractInput
    response_format: str = "content_and_artifact"

    actor_id: str = DEFAULT_WEB_CRAWLER_ACTOR
    _client: ApifyClientAsync | None = PrivateAttr(default=None)

    def __init__(
        self,
        client: ApifyClientAsync | None = None,
        actor_id: str = DEFAULT_WEB_CRAWLER_ACTOR,
        **kwargs: Any,
    ):
        super().__init__(actor_id=actor_id, **kwargs)
        self._client = client

    @property
    def client(self) -> ApifyClientAsync:
        if self._client is None:
            self._client = _build_client()
        return self._client

    def _run(self, *args, **kwargs):
        raise NotImplementedError("Use async invocation.")

    async def _arun(
        self,
        urls: list[str],
        run_manager: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict]:
        # cheerio = HTTP-only fetch (~$0.0002/URL); maxCrawlDepth=0 disables
        # link-following so we get exactly the URLs we asked for.
        run_input = {
            "startUrls": [{"url": u} for u in urls],
            "maxCrawlPages": len(urls),
            "maxCrawlDepth": 0,
            "crawlerType": "cheerio",
            "saveMarkdown": True,
            "saveHtml": False,
        }
        result = await _run_actor(self.client, self.actor_id, run_input)
        results = []
        for it in result["items"]:
            results.append({
                "url": it.get("url"),
                "raw_content": it.get("markdown") or it.get("text") or "",
            })
        artifact = {"results": results, "raw": result["raw"]}
        content = json.dumps({"results": results}, default=str, ensure_ascii=False)
        return content, artifact


# ---------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------


class ApifyInstagramInput(BaseModel):
    username: str = Field(description="Instagram username, without the @.")
    results_limit: int = Field(default=20, ge=1, le=50)


_IG_DESCRIPTION = (
    "Fetch a public Instagram profile and the user's recent posts via Apify's "
    "instagram-scraper actor. Use when you have a confirmed Instagram handle."
    "\n\nReturns:\n"
    "- Profile: username, full name, biography, follower/following counts, "
    "verification status, business-account flag, external URL, profile "
    "pictures (standard and HD), total post count, IGTV count.\n"
    "- Per post: caption, hashtags, user mentions, like/comment/view counts, "
    "timestamp, image dimensions, display/media URLs, post type "
    "(Image / Video / Sidecar carousel), location data, sponsored flag.\n\n"
    "Inputs: `username` (without @, required) and `results_limit` (1-50, "
    "default 20) for the number of recent posts. The underlying actor also "
    "supports hashtag search, place search, and post-URL fetch — those are "
    "not exposed in v1; if you need them, prefer `web_search` for the URL "
    "discovery and call this tool by handle."
)


class ApifyInstagramTool(BaseTool):
    name: str = "apify_instagram"
    description: str = _IG_DESCRIPTION
    args_schema: Type[BaseModel] = ApifyInstagramInput
    response_format: str = "content_and_artifact"

    actor_id: str = DEFAULT_IG_ACTOR
    _client: ApifyClientAsync | None = PrivateAttr(default=None)

    def __init__(
        self,
        client: ApifyClientAsync | None = None,
        actor_id: str = DEFAULT_IG_ACTOR,
        **kwargs: Any,
    ):
        super().__init__(actor_id=actor_id, **kwargs)
        self._client = client

    @property
    def client(self) -> ApifyClientAsync:
        if self._client is None:
            self._client = _build_client()
        return self._client

    def _run(self, *args, **kwargs):
        raise NotImplementedError("Use async invocation.")

    async def _arun(
        self,
        username: str,
        results_limit: int = 20,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict]:
        # Use `directUrls` + `resultsType="details"` instead of the actor's
        # `usernames` field. Verified live against apify/instagram-scraper
        # (2026-04): the `usernames` -> URL builder silently DROPS handles
        # containing dots (e.g. `simonwen.eth`, common ENS-style IG
        # handles), returning {"error":"no_items","errorDescription":
        # "Empty or private data for provided input"} even for fully
        # public accounts. directUrls bypasses the bug; resultsType
        # "details" returns the profile fields plus the most recent ~12
        # posts inline so we don't lose post content vs the old shape.
        run_input = {
            "directUrls": [f"https://www.instagram.com/{username}/"],
            "resultsType": "details",
            "resultsLimit": results_limit,
        }
        result = await _run_actor(self.client, self.actor_id, run_input)
        content = json.dumps({"username": username, "items": result["items"]}, default=str)
        return content, result


# ---------------------------------------------------------------------------
# LinkedIn
# ---------------------------------------------------------------------------


class ApifyLinkedInInput(BaseModel):
    profile_url: str = Field(description="Full https://www.linkedin.com/in/<slug>/ URL.")


_LI_DESCRIPTION = (
    "Fetch a LinkedIn profile by its public URL via an Apify scraper actor "
    "(no LinkedIn login required). Use when you have a confirmed LinkedIn "
    "profile URL.\n\nReturns:\n"
    "- Identity: full name, headline, summary/about, location, profile "
    "pictures, public identifier, LinkedIn URN, connection count, follower "
    "count.\n"
    "- Work history: every position — title, company, description, "
    "start/end dates, employment status, location.\n"
    "- Companies referenced: name, industry, website, LinkedIn URL, size "
    "range, founding year.\n"
    "- Education: institutions, degrees, fields of study, dates.\n"
    "- Skills (with endorsement counts), languages, certifications, "
    "publications, patents, volunteer experience, recommendations.\n"
    "- Some actors also attempt email-address discovery for the profile.\n\n"
    "Input: `profile_url` — the full https://www.linkedin.com/in/<slug>/ URL. "
    "The actor does NOT support free-text people search by name; discover "
    "the URL first via `web_search` and then call this tool."
)


class ApifyLinkedInTool(BaseTool):
    name: str = "apify_linkedin"
    description: str = _LI_DESCRIPTION
    args_schema: Type[BaseModel] = ApifyLinkedInInput
    response_format: str = "content_and_artifact"

    actor_id: str = DEFAULT_LI_ACTOR
    _client: ApifyClientAsync | None = PrivateAttr(default=None)

    def __init__(
        self,
        client: ApifyClientAsync | None = None,
        actor_id: str = DEFAULT_LI_ACTOR,
        **kwargs: Any,
    ):
        super().__init__(actor_id=actor_id, **kwargs)
        self._client = client

    @property
    def client(self) -> ApifyClientAsync:
        if self._client is None:
            self._client = _build_client()
        return self._client

    def _run(self, *args, **kwargs):
        raise NotImplementedError("Use async invocation.")

    async def _arun(
        self,
        profile_url: str,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict]:
        run_input = {"profileUrls": [profile_url]}
        result = await _run_actor(self.client, self.actor_id, run_input)
        content = json.dumps({"profile_url": profile_url, "items": result["items"]}, default=str)
        return content, result


# ---------------------------------------------------------------------------
# Twitter / X
# ---------------------------------------------------------------------------


class ApifyTwitterInput(BaseModel):
    handle: str | None = Field(default=None, description="X handle without @.")
    search_query: str | None = Field(default=None, description="X search query.")
    max_items: int = Field(default=20, ge=1, le=100)


_TW_DESCRIPTION = (
    "Fetch X (Twitter) content via an Apify scraper actor. Use this for ANY "
    "X-native content — X's public surface is poorly indexed by general web "
    "search.\n\nTwo input modes (mutually exclusive):\n"
    "- `handle` (without @): fetches the user's profile and their recent "
    "tweets. Pass this to map a known X account.\n"
    "- `search_query`: searches tweets across all of X. Supports Twitter "
    "advanced search syntax (e.g. `from:nasa mars`, `\"jane doe\" "
    "since:2025-01-01`, `to:username`, hashtags). Pass this to find posts "
    "mentioning the subject across X.\n\nReturns:\n"
    "- Per tweet: tweet ID, URL, text, language, creation timestamp, "
    "engagement counts (likes, retweets, replies, quotes, bookmarks), media "
    "attachments, geolocation when present, conversation/thread context.\n"
    "- Author per tweet: handle, display name, follower count, verification "
    "status (standard and Blue), profile picture.\n"
    "- For handle mode: profile metadata (handle, name, follower/following "
    "counts, verification, profile pictures) alongside the recent-tweet "
    "stream.\n\nOptional `max_items` (1-100, default 20) caps the result set "
    "per call. The underlying actor also supports tweet-URL fetch and "
    "conversation-ID fetch; those are not exposed in v1 — use the search "
    "syntax (`conversation_id:...`, direct tweet URL search) if you need them."
)


class ApifyTwitterTool(BaseTool):
    name: str = "apify_twitter"
    description: str = _TW_DESCRIPTION
    args_schema: Type[BaseModel] = ApifyTwitterInput
    response_format: str = "content_and_artifact"

    actor_id: str = DEFAULT_TW_ACTOR
    _client: ApifyClientAsync | None = PrivateAttr(default=None)

    def __init__(
        self,
        client: ApifyClientAsync | None = None,
        actor_id: str = DEFAULT_TW_ACTOR,
        **kwargs: Any,
    ):
        super().__init__(actor_id=actor_id, **kwargs)
        self._client = client

    @property
    def client(self) -> ApifyClientAsync:
        if self._client is None:
            self._client = _build_client()
        return self._client

    def _run(self, *args, **kwargs):
        raise NotImplementedError("Use async invocation.")

    async def _arun(
        self,
        handle: str | None = None,
        search_query: str | None = None,
        max_items: int = 20,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict]:
        if bool(handle) == bool(search_query):
            raise ValueError(
                "ApifyTwitterTool requires exactly one of `handle` or "
                "`search_query` (got both or neither)."
            )

        # gentle_cloud~twitter-tweets-scraper input shape (verified live
        # 2026-04-26 against real @semona0x lookups). Required fields:
        #   start_urls   — list of {"url": "https://x.com/<handle>"} entries
        #   since_date   — REQUIRED. If omitted the actor's date filter drops
        #                  every tweet and the dataset returns simulation
        #                  placeholders ({"_simulation": true ...}). 2020-01-01
        #                  is wide enough for any real account history.
        #   result_count — string-typed cap on returned tweets.
        # We swapped from apidojo~twitter-scraper-lite which silently returns
        # demo data ({"demo": true}) on STARTER-plan accounts — the actor
        # errors with "Access to this origin is disabled" but reports
        # SUCCEEDED, so the agent treats placeholders as real data.
        if handle:
            url = f"https://x.com/{handle.lstrip('@')}"
        else:
            url = f"https://x.com/search?q={search_query}&src=typed_query"
        run_input: dict[str, Any] = {
            "start_urls": [{"url": url}],
            "since_date": "2020-01-01",
            "result_count": str(max_items),
        }

        result = await _run_actor(self.client, self.actor_id, run_input)
        content = json.dumps(
            {
                "handle": handle,
                "search_query": search_query,
                "items": result["items"],
            },
            default=str,
        )
        return content, result


# ---------------------------------------------------------------------------
# Lazy client construction
# ---------------------------------------------------------------------------


def _build_client() -> ApifyClientAsync:
    token = os.getenv("APIFY_TOKEN")
    if not token:
        raise ScanConfigError(
            "APIFY_TOKEN environment variable is not set; cannot build "
            "ApifyClientAsync."
        )
    return ApifyClientAsync(token=token)
