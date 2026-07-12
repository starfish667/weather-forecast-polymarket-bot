from __future__ import annotations

from decimal import Decimal

from weather_polymarket_bot.config import OpenMeteoConfig
from weather_polymarket_bot.open_meteo import (
    build_forecast_url,
    location_for_city,
    observations_from_payload,
)


def test_location_lookup_is_case_insensitive() -> None:
    location = location_for_city("hong kong")

    assert location.name == "Hong Kong"
    assert location.timezone == "Asia/Hong_Kong"


def test_build_forecast_url_uses_daily_max_and_timezone() -> None:
    config = OpenMeteoConfig(
        cities=["London"],
        forecast_days=2,
        daily_variable="temperature_2m_max",
        endpoint="https://api.open-meteo.com/v1/forecast",
    )

    url = build_forecast_url(config=config, location=location_for_city("London"))

    assert "daily=temperature_2m_max" in url
    assert "forecast_days=2" in url
    assert "timezone=Europe%2FLondon" in url


def test_observations_from_payload() -> None:
    payload = {
        "daily": {
            "time": ["2026-07-12", "2026-07-13"],
            "temperature_2m_max": [12.8, 14.2],
        }
    }

    observations = observations_from_payload(
        city="Buenos Aires",
        payload=payload,
        daily_variable="temperature_2m_max",
    )

    assert [(item.target_label, item.forecast_c, item.buckets_c) for item in observations] == [
        ("2026-07-12", Decimal("12.8"), [12, 13, 14]),
        ("2026-07-13", Decimal("14.2"), [13, 14, 15]),
    ]

