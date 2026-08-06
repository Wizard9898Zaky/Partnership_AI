"""
conversation_engine/tools/web_tools.py
Web search and URL fetch actions for Partnership_AI.

Actions:
  web_search       — Search the web via DuckDuckGo's HTML endpoint (no API key needed)
  fetch_url        — Fetch and return the text content of a URL

These actions give the agent real-time access to information outside its
own files, which is critical for answering questions about current events,
looking up documentation, and verifying facts.
"""
from __future__ import annotations
from typing import Any, Dict, List
import urllib.request
import urllib.parse
import re
import json
import logging

from conversation_engine.action_registry import ActionResult, register_action

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_MAX_RESPONSE_SIZE = 50_000       # 50KB max per fetch — prevents context overflow
_REQUEST_TIMEOUT = 15            # seconds
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) Partnership_AI/1.0 "
    "(+https://github.com/Partnership_AI)"
)

# ─────────────────────────────────────────────────────────────────────────────
# Helper: safe HTTP GET
# ─────────────────────────────────────────────────────────────────────────────

def _http_get(url: str, timeout: int = _REQUEST_TIMEOUT) -> bytes:
    """
    Perform a safe HTTP GET request.

    Args:
        url:     The URL to fetch. Must start with http:// or https://.
        timeout: Maximum seconds to wait for a response.

    Returns:
        Raw response bytes (truncated to _MAX_RESPONSE_SIZE).

    Raises:
        ValueError: If the URL scheme is not http/https.
        urllib.error.URLError: On network errors.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https URLs are allowed (got: {parsed.scheme})")
    if not parsed.hostname:
        raise ValueError("URL must have a hostname")

    req = urllib.request.Request(url, headers={
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/json,text/plain,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(_MAX_RESPONSE_SIZE + 1)
        if len(data) > _MAX_RESPONSE_SIZE:
            data = data[:_MAX_RESPONSE_SIZE]
            logger.info("[web_tools] Response truncated to %d bytes", _MAX_RESPONSE_SIZE)
        return data


# ─────────────────────────────────────────────────────────────────────────────
# Helper: strip HTML tags (lightweight, no dependencies)
# ─────────────────────────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    """
    Remove HTML tags and collapse whitespace to produce readable text.

    This is intentionally lightweight (no BeautifulSoup dependency).
    It strips scripts/styles, removes tags, and normalizes whitespace.

    Args:
        html: Raw HTML string.

    Returns:
        Cleaned plain text.
    """
    # Remove script and style blocks entirely
    html = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
    # Replace <br>, <p>, <div> with newlines for basic structure
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</(p|div|li|h[1-6])>', '\n', html, flags=re.IGNORECASE)
    # Strip all remaining tags
    html = re.sub(r'<[^>]+>', '', html)
    # Decode common HTML entities
    html = html.replace('&nbsp;', ' ').replace('&amp;', '&')
    html = html.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
    html = html.replace('&#39;', "'")
    # Collapse whitespace
    html = re.sub(r'[ \t]+', ' ', html)
    html = re.sub(r'\n{3,}', '\n\n', html)
    return html.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# Action: web_search
# ═══════════════════════════════════════════════════════════════════════════════

@register_action(
    "web_search",
    input_schema={
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "description": "Max results (default 5)"},
        },
    },
)
def web_search_action(query: str, max_results: int = 5) -> ActionResult:
    """
    Search the web using DuckDuckGo's HTML endpoint.

    Returns up to ``max_results`` results, each with a title, URL, and snippet.
    No API key required — uses DuckDuckGo's public HTML interface.

    Args:
        query:      The search query string.
        max_results: Maximum number of results to return (default 5, max 10).

    Returns:
        ActionResult with data['results'] = list of {title, url, snippet} dicts.
    """
    max_results = min(max(max_results, 1), 10)

    try:
        # DuckDuckGo HTML search endpoint
        search_url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({"q": query}).encode("utf-8")
        req = urllib.request.Request(search_url, data=data, method="POST", headers={
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        })

        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            html = resp.read(_MAX_RESPONSE_SIZE).decode("utf-8", errors="replace")

        # Parse results from DDG HTML
        results = []
        # DDG results are in <a class="result__a" href="...">title</a>
        # with snippets in <a class="result__snippet" ...>text</a>
        link_pattern = re.compile(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        snippet_pattern = re.compile(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )

        links = link_pattern.findall(html)
        snippets = snippet_pattern.findall(html)

        for i, (url_raw, title_raw) in enumerate(links[:max_results]):
            # DDG wraps URLs in a redirect: //duckduckgo.com/l/?uddg=ENCODED_URL
            if "uddg=" in url_raw:
                parsed_q = urllib.parse.parse_qs(urllib.parse.urlparse(url_raw).query)
                url_clean = parsed_q.get("uddg", [url_raw])[0]
            else:
                url_clean = url_raw

            title = _strip_html(title_raw)
            snippet = _strip_html(snippets[i]) if i < len(snippets) else ""

            results.append({
                "title": title[:200],
                "url": url_clean,
                "snippet": snippet[:300],
            })

        if not results:
            return ActionResult(
                success=True,
                data={"results": [], "message": f"No results found for '{query}'."},
            )

        return ActionResult(
            success=True,
            data={
                "query": query,
                "results": results,
                "count": len(results),
            },
        )

    except Exception as e:
        logger.exception("web_search failed")
        return ActionResult(
            success=False,
            error=f"Web search failed: {e}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Action: fetch_url
# ═══════════════════════════════════════════════════════════════════════════════

@register_action(
    "fetch_url",
    input_schema={
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "raw": {"type": "boolean", "description": "If true, return raw HTML instead of cleaned text"},
        },
    },
)
def fetch_url_action(url: str, raw: bool = False) -> ActionResult:
    """
    Fetch the content of a URL and return it as text.

    By default, HTML is stripped to plain text for readability.
    Set ``raw=True`` to get the original HTML (useful for structured parsing).

    Enforces a 50KB response size limit to prevent context window overflow.

    Args:
        url: The URL to fetch (must start with http:// or https://).
        raw: If True, return raw HTML instead of cleaned text (default False).

    Returns:
        ActionResult with data['content'] = the page text, data['url'] = the URL.
    """
    try:
        raw_bytes = _http_get(url)
        content = raw_bytes.decode("utf-8", errors="replace")

        if not raw:
            # Check if it looks like HTML
            if "<html" in content.lower() or "<!doctype html" in content.lower():
                content = _strip_html(content)
            # JSON responses — try to pretty-print
            elif content.strip().startswith("{") or content.strip().startswith("["):
                try:
                    content = json.dumps(json.loads(content), indent=2)
                except json.JSONDecodeError:
                    pass  # not valid JSON, leave as-is

        return ActionResult(
            success=True,
            data={
                "url": url,
                "content": content[:_MAX_RESPONSE_SIZE],
                "size_bytes": len(raw_bytes),
            },
        )

    except ValueError as e:
        return ActionResult(success=False, error=str(e))
    except Exception as e:
        logger.exception("fetch_url failed")
        return ActionResult(
            success=False,
            error=f"Failed to fetch URL: {e}",
        )
