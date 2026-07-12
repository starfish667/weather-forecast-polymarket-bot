from __future__ import annotations

import asyncio
import hashlib
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable

from polymarket import AsyncPublicClient

from weather_polymarket_bot.ai_research import AnalystReview, MarketContext, request_news_review
from weather_polymarket_bot.config import ZeroZeroConfig


MAX_HEADLINE_TITLE_CHARS = 500
MAX_HEADLINE_LINK_CHARS = 1000
MAX_HEADLINE_PUBLISHED_CHARS = 200


@dataclass(frozen=True)
class Headline:
    title: str
    link: str
    published: str

    @property
    def fingerprint(self) -> str:
        value = f"{self.title}\n{self.link}".encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    def as_prompt_data(self) -> dict[str, str]:
        return {"title": self.title, "link": self.link, "published": self.published}


@dataclass(frozen=True)
class NewsReviewRound:
    headline_count: int
    market_count: int
    review: AnalystReview | None
    feed_errors: tuple[str, ...]


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, name: str) -> str:
    for child in element:
        if _local_name(child) == name:
            return "".join(child.itertext()).strip()
    return ""


def parse_headlines(payload: bytes, *, limit: int) -> list[Headline]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise RuntimeError("News feed was not valid XML") from error
    entries = [element for element in root.iter() if _local_name(element) in {"item", "entry"}]
    headlines: list[Headline] = []
    for entry in entries:
        title = _child_text(entry, "title")
        if not title:
            continue
        link = _child_text(entry, "link")
        if not link:
            for child in entry:
                if _local_name(child) == "link":
                    link = child.attrib.get("href", "")
                    if link:
                        break
        published = _child_text(entry, "published") or _child_text(entry, "updated")
        headlines.append(
            Headline(
                title=title[:MAX_HEADLINE_TITLE_CHARS],
                link=link[:MAX_HEADLINE_LINK_CHARS],
                published=published[:MAX_HEADLINE_PUBLISHED_CHARS],
            )
        )
        if len(headlines) == limit:
            break
    return headlines


def fetch_feed_headlines(url: str, *, limit: int) -> list[Headline]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "polymarket-news-monitor/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read()
    except OSError as error:
        raise RuntimeError(f"News feed request failed for {url}: {error}") from error
    return parse_headlines(payload, limit=limit)


async def active_event_context(*, max_events: int) -> list[MarketContext]:
    async with AsyncPublicClient() as client:
        page = await client.list_events(
            closed=False,
            order="volume",
            page_size=max_events,
        ).first_page()
    events = [
        MarketContext(slug=str(event.slug), title=str(event.title))
        for event in page.items
        if getattr(event, "slug", None)
        and getattr(event, "title", None)
        and getattr(event.state, "active", False)
        and not getattr(event.state, "closed", False)
    ]
    return events[:max_events]


async def collect_headlines(
    feeds: Iterable[str],
    *,
    max_headlines_per_feed: int,
) -> tuple[list[Headline], tuple[str, ...]]:
    batches = await asyncio.gather(
        *(
            asyncio.to_thread(fetch_feed_headlines, url, limit=max_headlines_per_feed)
            for url in feeds
        ),
        return_exceptions=True,
    )
    unique: dict[str, Headline] = {}
    errors: list[str] = []
    for batch in batches:
        if isinstance(batch, Exception):
            errors.append(str(batch))
            continue
        for headline in batch:
            unique.setdefault(headline.fingerprint, headline)
    return list(unique.values()), tuple(errors)


async def run_news_review_round(
    *,
    config: ZeroZeroConfig,
    feeds: Iterable[str],
    seen_headlines: set[str] | None = None,
) -> NewsReviewRound:
    headlines, feed_errors = await collect_headlines(
        feeds,
        max_headlines_per_feed=config.max_headlines_per_feed,
    )
    if seen_headlines is not None:
        new_headlines = [headline for headline in headlines if headline.fingerprint not in seen_headlines]
        seen_headlines.update(headline.fingerprint for headline in headlines)
        headlines = new_headlines
    events = await active_event_context(max_events=config.max_events)
    review = None
    if headlines and events:
        review = await asyncio.to_thread(
            request_news_review,
            config=config,
            headlines=(headline.as_prompt_data() for headline in headlines),
            events=events,
        )
    return NewsReviewRound(
        headline_count=len(headlines),
        market_count=len(events),
        review=review,
        feed_errors=feed_errors,
    )
