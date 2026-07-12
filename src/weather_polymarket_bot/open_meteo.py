from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from weather_polymarket_bot.config import OpenMeteoConfig
from weather_polymarket_bot.models import ForecastObservation, utc_now


@dataclass(frozen=True)
class CityLocation:
    name: str
    latitude: Decimal
    longitude: Decimal
    timezone: str


DEFAULT_LOCATIONS: dict[str, CityLocation] = {
    "los angeles": CityLocation(
        name="Los Angeles",
        latitude=Decimal("34.0522"),
        longitude=Decimal("-118.2437"),
        timezone="America/Los_Angeles",
    ),
    "buenos aires": CityLocation(
        name="Buenos Aires",
        latitude=Decimal("-34.6037"),
        longitude=Decimal("-58.3816"),
        timezone="America/Argentina/Buenos_Aires",
    ),
    "london": CityLocation(
        name="London",
        latitude=Decimal("51.5072"),
        longitude=Decimal("-0.1276"),
        timezone="Europe/London",
    ),
    "hong kong": CityLocation(
        name="Hong Kong",
        latitude=Decimal("22.3193"),
        longitude=Decimal("114.1694"),
        timezone="Asia/Hong_Kong",
    ),
}


def normalize_key(value: str) -> str:
    return " ".join(value.strip().lower().split())


def location_for_city(city: str) -> CityLocation:
    key = normalize_key(city)
    try:
        return DEFAULT_LOCATIONS[key]
    except KeyError as error:
        known = ", ".join(sorted(location.name for location in DEFAULT_LOCATIONS.values()))
        raise RuntimeError(f"Unknown forecast city {city!r}. Known cities: {known}") from error


def build_forecast_url(
    *,
    config: OpenMeteoConfig,
    location: CityLocation,
) -> str:
    params = {
        "latitude": str(location.latitude),
        "longitude": str(location.longitude),
        "daily": config.daily_variable,
        "timezone": location.timezone,
        "forecast_days": str(config.forecast_days),
    }
    return f"{config.endpoint}?{urllib.parse.urlencode(params)}"


def fetch_json(url: str, *, timeout_seconds: float = 20.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "weather-forecast-polymarket-bot/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        data = response.read()
    return json.loads(data.decode("utf-8"))


def observations_from_payload(
    *,
    city: str,
    payload: dict[str, Any],
    daily_variable: str,
) -> list[ForecastObservation]:
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise RuntimeError("Open-Meteo response did not contain a daily forecast block")

    dates = daily.get("time")
    values = daily.get(daily_variable)
    if not isinstance(dates, list) or not isinstance(values, list):
        raise RuntimeError(f"Open-Meteo response did not contain daily {daily_variable!r}")

    fetched_at = utc_now()
    raw_text = json.dumps(payload, sort_keys=True)
    observations: list[ForecastObservation] = []
    for target_date, value in zip(dates, values, strict=False):
        if value is None:
            continue
        observations.append(
            ForecastObservation(
                source=f"open-meteo:{daily_variable}",
                city=city,
                forecast_c=Decimal(str(value)),
                raw_text=raw_text,
                fetched_at=fetched_at,
                target_label=str(target_date),
            )
        )
    return observations


def fetch_open_meteo_round(config: OpenMeteoConfig) -> list[ForecastObservation]:
    observations: list[ForecastObservation] = []
    for city in config.cities:
        location = location_for_city(city)
        url = build_forecast_url(config=config, location=location)
        payload = fetch_json(url)
        observations.extend(
            observations_from_payload(
                city=location.name,
                payload=payload,
                daily_variable=config.daily_variable,
            )
        )
    return observations

