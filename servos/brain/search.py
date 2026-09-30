from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import httpx

log = logging.getLogger("servos.search")

_HEADERS = {"User-Agent": "Pardilston/0.1 (local assistant)"}


def web_search(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "No query was provided."
    try:
        results = _results(query)
    except Exception as exc:
        log.warning("search failed: %s", exc)
        return f"Search failed: {exc}"
    if not results:
        return "No results."

    with ThreadPoolExecutor(max_workers=3) as pool:
        pages = list(pool.map(_fetch, results[:3]))
    body = "\n\n".join(page for page in pages if page)
    return (body or "No results.")[:3500]


def _results(query: str) -> list[dict]:
    from ddgs import DDGS

    client = DDGS(timeout=5)
    finder = getattr(client, "text", None)
    if finder is None:
        raw = client.search(query, max_results=4)
    else:
        try:
            raw = finder(query, max_results=4)
        except TypeError:
            raw = finder(query=query, max_results=4)
    return [item for item in list(raw)[:4] if isinstance(item, dict)]


def _fetch(item: dict) -> str:
    title = str(item.get("title") or "").strip()
    url = str(item.get("href") or item.get("url") or "").strip()
    snippet = str(item.get("body") or item.get("snippet") or "").strip()
    page = ""
    if url.startswith("http"):
        try:
            with httpx.Client(timeout=4.0, follow_redirects=True, headers=_HEADERS) as client:
                response = client.get(url)
            content_type = response.headers.get("content-type", "")
            if response.status_code == 200 and "text" in content_type:
                html = response.text[:400_000]
                import trafilatura

                extracted = trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=False,
                )
                if extracted:
                    page = extracted[:900]
        except Exception as exc:
            log.info("fetch skipped %s: %s", url, exc)
    text = page or snippet
    if not text and not title:
        return ""
    return f"{title}\n{url}\n{text}".strip()
