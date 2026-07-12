from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable

from polymarket import AsyncPublicClient

from weather_polymarket_bot.backtest import run_backtest
from weather_polymarket_bot.config import BacktestConfig
from weather_polymarket_bot.live_trading import city_from_event, event_target_date, normalized_city
from weather_polymarket_bot.market_backtest import (
    HistoricalBasket,
    HistoricalLeg,
    MarketBacktestRun,
    entry_time_for_target,
    historical_price_for_token,
)


ONE = Decimal("1")
FAHRENHEIT_SCALE = Decimal("9") / Decimal("5")
FAHRENHEIT_OFFSET = Decimal("32")
TEMPERATURE_LABEL_RE = re.compile(
    r"^\s*(?P<low>-?\d+)(?:\s*[-\u2013]\s*(?P<high>-?\d+))?\s*\u00b0(?P<unit>[CF])"
    r"(?:\s+or\s+(?P<tail>below|lower|higher|above))?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TemperatureMarket:
    market: object
    label: str
    lower: Decimal | None
    upper: Decimal | None
    unit: str

    def distance_to(self, value: Decimal) -> Decimal:
        if self.lower is not None and value < self.lower:
            return self.lower - value
        if self.upper is not None and value > self.upper:
            return value - self.upper
        return Decimal("0")


def parse_temperature_market(market: object) -> TemperatureMarket | None:
    label = getattr(market, "group_item_title", None)
    if not isinstance(label, str):
        return None
    match = TEMPERATURE_LABEL_RE.fullmatch(label)
    if match is None:
        return None
    low = Decimal(match.group("low"))
    high = Decimal(match.group("high")) if match.group("high") is not None else low
    tail = match.group("tail")
    if tail in {"below", "lower"}:
        lower, upper = None, high
    elif tail in {"higher", "above"}:
        lower, upper = low, None
    else:
        lower, upper = low, high
    return TemperatureMarket(
        market=market,
        label=label,
        lower=lower,
        upper=upper,
        unit=match.group("unit").upper(),
    )


def forecast_in_unit(forecast_c: Decimal, *, unit: str) -> Decimal:
    return forecast_c if unit == "C" else forecast_c * FAHRENHEIT_SCALE + FAHRENHEIT_OFFSET


def select_model_neighbor_markets(
    markets: Iterable[object],
    *,
    forecast_c: Decimal,
) -> tuple[object, ...] | None:
    parsed = [item for market in markets if (item := parse_temperature_market(market)) is not None]
    if len(parsed) < 3 or len({item.unit for item in parsed}) != 1:
        return None
    ordered = sorted(
        parsed,
        key=lambda item: item.lower if item.lower is not None else Decimal("-1000"),
    )
    forecast = forecast_in_unit(forecast_c, unit=ordered[0].unit)
    center_index = min(
        range(len(ordered)),
        key=lambda index: (ordered[index].distance_to(forecast), index),
    )
    start = max(0, min(center_index - 1, len(ordered) - 3))
    return tuple(item.market for item in ordered[start : start + 3])


async def model_basket_for_event(
    *,
    client: AsyncPublicClient,
    event: object,
    city: str,
    target_date: date,
    forecast_c: Decimal,
    entry_hour_utc: int,
    lookback_hours: int,
    semaphore: asyncio.Semaphore,
) -> HistoricalBasket | None:
    event_slug = getattr(event, "slug", None)
    selected = select_model_neighbor_markets(
        getattr(event, "markets", ()) or (),
        forecast_c=forecast_c,
    )
    if not event_slug or selected is None:
        return None
    token_ids = [getattr(market.outcomes.yes, "token_id", None) for market in selected]
    if any(token_id is None for token_id in token_ids):
        return None
    entered_at = entry_time_for_target(target_date, entry_hour_utc=entry_hour_utc)
    prices = await asyncio.gather(
        *(
            historical_price_for_token(
                client=client,
                token_id=str(token_id),
                entered_at=entered_at,
                lookback_hours=lookback_hours,
                semaphore=semaphore,
            )
            for token_id in token_ids
        ),
        return_exceptions=True,
    )
    if any(isinstance(price, Exception) or price is None for price in prices):
        return None

    legs: list[HistoricalLeg] = []
    for market, price in zip(selected, prices, strict=True):
        resolution_price = getattr(market.outcomes.yes, "price", None)
        if resolution_price is None or not isinstance(price, Decimal):
            return None
        legs.append(
            HistoricalLeg(
                label=market.group_item_title or market.question or str(market.id),
                price=price,
                resolved_yes=Decimal(str(resolution_price)) == ONE,
                fees_enabled=bool(getattr(market.trading, "fees_enabled", False)),
            )
        )
    return HistoricalBasket(
        event_slug=str(event_slug),
        city=city,
        target_date=target_date,
        entered_at=entered_at,
        legs=tuple(legs),
    )


async def run_model_market_backtest(
    *,
    config: BacktestConfig,
    start_date: date,
    end_date: date,
    entry_hour_utc: int = 12,
    lookback_hours: int = 6,
    concurrency: int = 6,
) -> MarketBacktestRun:
    if end_date < start_date:
        raise RuntimeError("Backtest end date must be on or after the start date")
    if not 0 <= entry_hour_utc <= 23:
        raise RuntimeError("entry_hour_utc must be between 0 and 23")
    if lookback_hours < 1:
        raise RuntimeError("lookback_hours must be at least 1")
    if concurrency < 1:
        raise RuntimeError("concurrency must be at least 1")

    forecasts = await asyncio.to_thread(
        run_backtest,
        config=config,
        start_date=start_date,
        end_date=end_date,
    )
    forecasts_by_key = {
        (normalized_city(result.city), result.target_date): result.forecast_c for result in forecasts
    }
    allowed_cities = {normalized_city(city) for city in config.cities}
    candidates: list[tuple[object, str, date, Decimal]] = []
    async with AsyncPublicClient() as client:
        for offset in range((end_date - start_date).days + 1):
            requested_date = start_date + timedelta(days=offset)
            page = await client.list_events(
                closed=True,
                event_date=requested_date,
                tag_slug="weather",
                page_size=100,
            ).first_page()
            for event in page.items:
                city = city_from_event(event)
                target_date = event_target_date(event)
                forecast_c = (
                    forecasts_by_key.get((normalized_city(city), target_date))
                    if city is not None and target_date is not None
                    else None
                )
                if (
                    city is not None
                    and normalized_city(city) in allowed_cities
                    and target_date == requested_date
                    and forecast_c is not None
                ):
                    candidates.append((event, city, target_date, forecast_c))

        semaphore = asyncio.Semaphore(concurrency)
        results = await asyncio.gather(
            *(
                model_basket_for_event(
                    client=client,
                    event=event,
                    city=city,
                    target_date=target_date,
                    forecast_c=forecast_c,
                    entry_hour_utc=entry_hour_utc,
                    lookback_hours=lookback_hours,
                    semaphore=semaphore,
                )
                for event, city, target_date, forecast_c in candidates
            ),
            return_exceptions=True,
        )

    baskets = [result for result in results if isinstance(result, HistoricalBasket)]
    return MarketBacktestRun(
        matched_events=len(candidates),
        skipped_events=len(candidates) - len(baskets),
        baskets=tuple(sorted(baskets, key=lambda basket: (basket.target_date, basket.city))),
    )
