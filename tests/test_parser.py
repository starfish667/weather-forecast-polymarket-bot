from __future__ import annotations

from decimal import Decimal

from weather_polymarket_bot.models import nearby_buckets
from weather_polymarket_bot.parser import parse_forecasts


def test_nearby_buckets_rounds_half_up() -> None:
    assert nearby_buckets(Decimal("12.8")) == [12, 13, 14]
    assert nearby_buckets(Decimal("12.5")) == [12, 13, 14]
    assert nearby_buckets(Decimal("12.4")) == [11, 12, 13]


def test_parse_city_temperature_lines() -> None:
    text = """
    Los Angeles - 22.8C
    Buenos Aires - 12.8C
    London - 19.1°C
    Hong Kong - 29.6 C
    """

    forecasts = parse_forecasts(text)

    assert [(item.city, item.forecast_c, item.buckets_c) for item in forecasts] == [
        ("Los Angeles", Decimal("22.8"), [22, 23, 24]),
        ("Buenos Aires", Decimal("12.8"), [12, 13, 14]),
        ("London", Decimal("19.1"), [18, 19, 20]),
        ("Hong Kong", Decimal("29.6"), [29, 30, 31]),
    ]


def test_parse_temperature_before_city() -> None:
    forecasts = parse_forecasts("Forecast: 12.8C for Buenos Aires")

    assert len(forecasts) == 1
    assert forecasts[0].city == "Buenos Aires"
    assert forecasts[0].forecast_c == Decimal("12.8")


def test_does_not_parse_city_time_menu() -> None:
    text = """
    Los Angeles - 08:56
    Buenos Aires - 12:56
    London - 16:56
    Hong Kong - 23:56
    """

    assert parse_forecasts(text) == []

