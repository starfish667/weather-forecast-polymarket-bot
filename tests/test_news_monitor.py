from __future__ import annotations

import asyncio

from weather_polymarket_bot import news_monitor
from weather_polymarket_bot.news_monitor import parse_headlines


def test_parse_rss_headlines() -> None:
    headlines = parse_headlines(
        b"""
        <rss><channel>
          <item><title>First headline</title><link>https://example.com/1</link><pubDate>Now</pubDate></item>
          <item><title>Second headline</title><link>https://example.com/2</link></item>
        </channel></rss>
        """,
        limit=1,
    )

    assert len(headlines) == 1
    assert headlines[0].title == "First headline"
    assert headlines[0].link == "https://example.com/1"


def test_collect_headlines_keeps_working_when_one_feed_fails(monkeypatch) -> None:
    def fetch(url: str, *, limit: int):
        if url == "bad":
            raise RuntimeError("bad feed")
        return parse_headlines(
            b"<rss><channel><item><title>Good</title></item></channel></rss>",
            limit=limit,
        )

    monkeypatch.setattr(news_monitor, "fetch_feed_headlines", fetch)

    headlines, errors = asyncio.run(
        news_monitor.collect_headlines(["good", "bad"], max_headlines_per_feed=2)
    )

    assert [headline.title for headline in headlines] == ["Good"]
    assert errors == ("bad feed",)
