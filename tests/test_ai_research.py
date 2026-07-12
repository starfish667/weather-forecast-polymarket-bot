from __future__ import annotations

import pytest

from weather_polymarket_bot import ai_research
from weather_polymarket_bot.ai_research import MarketContext, parse_review_content, request_news_review
from weather_polymarket_bot.config import ZeroZeroConfig


def test_review_parser_filters_unknown_event_slugs() -> None:
    review = parse_review_content(
        '{"summary":"Review the policy event.","event_slugs":["known","unknown"],'
        '"evidence":["headline"],"uncertainties":["source timing"]}',
        allowed_event_slugs=["known"],
    )

    assert review.summary == "Review the policy event."
    assert review.event_slugs == ("known",)
    assert review.evidence == ("headline",)


def test_review_parser_rejects_non_json_output() -> None:
    with pytest.raises(RuntimeError, match="valid JSON"):
        parse_review_content("not json", allowed_event_slugs=[])


def test_request_news_review_uses_openai_compatible_bearer_request(monkeypatch) -> None:
    captured = {}

    class Response:
        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"{\\"summary\\":\\"Review.\\",\\"event_slugs\\":[\\"event\\"],\\"evidence\\":[],\\"uncertainties\\":[]}"}}]}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, *, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(ai_research.urllib.request, "urlopen", urlopen)
    config = ZeroZeroConfig(
        api_key="test-key",
        base_url="https://api.0-0.pro/v1",
        model="gpt-5.5",
        timeout_seconds=12,
        news_feeds=[],
        max_events=5,
        max_headlines_per_feed=5,
    )

    review = request_news_review(
        config=config,
        headlines=[{"title": "headline", "link": "", "published": ""}],
        events=[MarketContext(slug="event", title="Event")],
    )

    assert review.event_slugs == ("event",)
    assert captured == {
        "url": "https://api.0-0.pro/v1/chat/completions",
        "authorization": "Bearer test-key",
        "timeout": 12,
    }
