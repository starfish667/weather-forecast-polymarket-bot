from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from weather_polymarket_bot.models import ForecastObservation, utc_now


FORECAST_PATTERNS = (
    re.compile(
        r"(?P<temp>[+-]?\d+(?:\.\d+)?)\s*(?:°\s*)?C"
        r".{0,30}?\b(?:in|for)\s+(?P<city>[A-Z][A-Za-z .'-]{1,40})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<city>[A-Z][A-Za-z .'-]{1,40}?)"
        r"(?:\s*[-–:|]\s*|\s+)"
        r"(?P<temp>[+-]?\d+(?:\.\d+)?)\s*(?:°\s*)?C\b",
        re.IGNORECASE,
    ),
)


def normalize_city(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.strip(" -–:|,.;"))
    return " ".join(part.capitalize() for part in cleaned.split(" "))


def parse_forecasts(
    text: str,
    *,
    source: str = "@weatherscan_bot",
    fetched_at: datetime | None = None,
    message_id: int | None = None,
) -> list[ForecastObservation]:
    observed_at = fetched_at or utc_now()
    forecasts: list[ForecastObservation] = []
    seen: set[tuple[str, Decimal]] = set()
    for line in text.splitlines():
        for pattern in FORECAST_PATTERNS:
            match = pattern.search(line)
            if match is None:
                continue
            city = normalize_city(match.group("city"))
            temp = Decimal(match.group("temp"))
            key = (city.lower(), temp)
            if key in seen:
                break
            seen.add(key)
            forecasts.append(
                ForecastObservation(
                    source=source,
                    city=city,
                    forecast_c=temp,
                    raw_text=text,
                    fetched_at=observed_at,
                    message_id=message_id,
                )
            )
            break
    return forecasts


def parse_many(messages: Iterable[tuple[str, int | None]]) -> list[ForecastObservation]:
    items: list[ForecastObservation] = []
    for text, message_id in messages:
        items.extend(parse_forecasts(text, message_id=message_id))
    return items
