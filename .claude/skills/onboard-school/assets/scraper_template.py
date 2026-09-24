"""Customize this skeleton, then register it with scraper_store.py."""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup


def parse_date(label: str) -> date | None:
    """Replace with deterministic formats used by this source."""
    raise NotImplementedError("implement the source's explicit date formats")


def scrape(ctx):
    pages = ctx.config.get("source_pages") or []
    if not pages:
        raise RuntimeError("config.source_pages is empty")

    boundary = date.today() - timedelta(days=365)
    emitted: set[str] = set()
    dated: list[date] = []
    undated = 0

    for page_url in pages:
        # HTTP is the default. Only substitute ctx.browser_get(page_url,
        # selector="...") when the registered, terms-approved source requires
        # JavaScript OR ctx.get(page_url) returned HTTP 403/406 and that result
        # is recorded in source notes. config.browser.enabled must be true. The
        # trusted runner supplies Bright Data; never put proxy credentials in
        # config, and fetch discovered document URLs through ctx.get when open.
        # If a plain browser_get() (config.browser.provider default,
        # "isp_proxy") still 403s/406s, retry it once with
        # actions=[{"wait_ms": 3000}] before reaching for "scraping_browser" —
        # it's often a page read before it finished loading, not a real wall,
        # and has fixed most real sources tried against this fallback so far.
        # If the real content only shows up after a click or a postback-driven
        # control (not merely after JS finishes), use actions=[...] for that
        # instead of hand-rolling anything; see references/scrapers.md.
        response = ctx.get(page_url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Replace these selectors with the stable structure verified for the
        # source. Never return success when the selector stops matching.
        rows = soup.select("TODO_MEETING_ROW_SELECTOR")
        if not rows:
            raise RuntimeError(f"meeting selector returned zero rows: {page_url}")

        for row in rows:
            link = row.select_one("TODO_DOCUMENT_LINK_SELECTOR")
            date_node = row.select_one("TODO_DATE_SELECTOR")
            if not link or not link.get("href"):
                ctx.log("skipping malformed row", row.get_text(" ", strip=True)[:160])
                continue
            meeting_date = parse_date(date_node.get_text(" ", strip=True)) if date_node else None
            if meeting_date and meeting_date < boundary:
                continue
            url = urljoin(page_url, link["href"])
            if url in emitted:
                continue
            emitted.add(url)
            if meeting_date:
                dated.append(meeting_date)
            else:
                undated += 1
            ctx.emit_meeting(
                document_url=url,
                meeting_date=meeting_date.isoformat() if meeting_date else None,
                title=row.get_text(" ", strip=True)[:300],
                kind="other",  # replace with agenda|minutes|packet|video
            )

    # Store the reviewed baseline as config.minimum_documents. The runner
    # applies the shared two-document rolling-window tolerance at execution;
    # do not bake a second tolerance into authored code.
    minimum_documents = int(ctx.config.get("minimum_documents", 1))
    if len(emitted) < minimum_documents:
        raise RuntimeError(
            f"document floor not met: found {len(emitted)}, expected {minimum_documents}"
        )
    if not emitted:
        raise RuntimeError("no documents emitted inside the trailing-year window")
    ctx.log(
        "emitted", len(emitted), "documents", "dated", len(dated), "undated", undated,
        "oldest", min(dated) if dated else None, "newest", max(dated) if dated else None,
    )
