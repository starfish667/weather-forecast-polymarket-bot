from __future__ import annotations

from weather_polymarket_bot.config import DEFAULT_NEWS_FEEDS, ZeroZeroConfig


def test_zero_zero_config_uses_ai_api_key_and_default_feeds(monkeypatch) -> None:
    monkeypatch.delenv("ZERO_ZERO_API_KEY", raising=False)
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.delenv("NEWS_FEEDS", raising=False)

    config = ZeroZeroConfig.from_env()

    assert config.api_key == "test-key"
    assert config.news_feeds == list(DEFAULT_NEWS_FEEDS)
