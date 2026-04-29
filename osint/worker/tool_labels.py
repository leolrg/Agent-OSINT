"""Server-side translation of internal tool names to user-facing labels.

Used by the worker's RedisEventSink to enrich tool.started / tool.finished
events with a `display_label` and `arg_summary` before publishing to Redis.
The UI renders these directly; internal tool names (apify_*, web_extract,
maigret, etc.) never reach the browser.

Adding a new tool: add an entry to TOOL_RENDERERS. If a tool ships before
its renderer, it falls through to ('Tool', '') — never leaks the internal
name.
"""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlparse


def _slug_from_linkedin_url(url: str) -> str:
    # https://www.linkedin.com/in/<slug>/[?...] -> <slug>
    try:
        path = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
        return path or url
    except Exception:
        return ""


def _domain_only(url: str) -> str:
    try:
        return urlparse(url).netloc or url
    except Exception:
        return ""


def _render_extract_label(args: dict[str, Any]) -> str:
    urls = args.get("urls") or []
    return "Read pages" if len(urls) > 1 else "Read page"


def _render_urls(args: dict[str, Any]) -> str:
    urls = args.get("urls") or []
    if not urls:
        return ""
    domains = [_domain_only(u) for u in urls]
    if len(domains) == 1:
        return domains[0]
    if len(domains) == 2:
        return ", ".join(domains)
    return f"{domains[0]}, {domains[1]} + {len(domains) - 2} more"


def _twitter_label(args: dict[str, Any]) -> str:
    if args.get("search_query"):
        return "X / Twitter search"
    return "X / Twitter"


def _render_twitter(args: dict[str, Any]) -> str:
    if args.get("search_query"):
        return str(args["search_query"])
    if args.get("handle"):
        return f"@{args['handle']}"
    return ""


# Each entry: (label_or_label_fn, arg_render_fn)
LabelOrFn = str | Callable[[dict[str, Any]], str]
ArgRenderer = Callable[[dict[str, Any]], str]

TOOL_RENDERERS: dict[str, tuple[LabelOrFn, ArgRenderer]] = {
    "web_search": (
        "Web search",
        lambda a: f'"{a["query"]}"' if a.get("query") else "",
    ),
    "web_extract": (_render_extract_label, _render_urls),
    "apify_linkedin": (
        "LinkedIn",
        lambda a: _slug_from_linkedin_url(a.get("profile_url", "")),
    ),
    "apify_instagram": (
        "Instagram",
        lambda a: a.get("username", "") or "",
    ),
    "apify_twitter": (_twitter_label, _render_twitter),
    "maigret": (
        "Username search",
        lambda a: a.get("username", "") or "",
    ),
}


def describe_tool_call(name: str, args: dict[str, Any]) -> tuple[str, str]:
    """Return (display_label, arg_summary) for a tool call.

    Unknown tool name -> ('Tool', '').
    Render exceptions -> graceful fallback to ('Tool', '').
    """
    renderer = TOOL_RENDERERS.get(name)
    if renderer is None:
        return ("Tool", "")
    label_part, arg_fn = renderer
    try:
        label = label_part(args) if callable(label_part) else label_part
        arg = arg_fn(args) or ""
    except Exception:
        return ("Tool", "")
    return (str(label), str(arg))
