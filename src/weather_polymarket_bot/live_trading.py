from __future__ import annotations

import asyncio
import os
import re
import urllib.parse
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Iterable

from polymarket import AsyncPublicClient, AsyncSecureClient

from weather_polymarket_bot.config import LiveTradingConfig, OpenMeteoConfig
from weather_polymarket_bot.models import nearby_buckets
from weather_polymarket_bot.open_meteo import fetch_json


GEOCODING_ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"
EXACT_TEMPERATURE_RE = re.compile(r"^(-?\d+)°C$")
WEATHER_EVENT_RE = re.compile(r"^Highest temperature in (?P<city>.+) on .+\?$")
ZERO = Decimal("0")
ONE = Decimal("1")
MIN_MARKETABLE_BUY_USD = Decimal("1")


@dataclass(frozen=True)
class AskLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class BasketLeg:
    bucket_c: int
    token_id: str
    question: str
    asks: tuple[AskLevel, ...]
    min_order_size: Decimal
    tick_size: Decimal


@dataclass(frozen=True)
class FilledLeg:
    leg: BasketLeg
    raw_cost: Decimal
    fee_cost: Decimal
    limit_price: Decimal


@dataclass(frozen=True)
class BasketQuote:
    event_slug: str
    city: str
    target_date: date
    forecast_c: Decimal
    shares: int
    legs: tuple[FilledLeg, ...]
    raw_cost: Decimal
    fee_cost: Decimal

    @property
    def all_in_cost(self) -> Decimal:
        return self.raw_cost + self.fee_cost

    @property
    def raw_cost_per_share(self) -> Decimal:
        return self.raw_cost / Decimal(self.shares)

    @property
    def all_in_cost_per_share(self) -> Decimal:
        return self.all_in_cost / Decimal(self.shares)

    @property
    def bucket_text(self) -> str:
        return "/".join(f"{leg.leg.bucket_c}C" for leg in self.legs)


def floor_integer(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def ceil_integer(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def exact_temperature_bucket(market: object) -> int | None:
    title = getattr(market, "group_item_title", None)
    if not isinstance(title, str):
        return None
    match = EXACT_TEMPERATURE_RE.fullmatch(title.strip())
    return int(match.group(1)) if match else None


def city_from_event(event: object) -> str | None:
    title = getattr(event, "title", None)
    if not isinstance(title, str):
        return None
    match = WEATHER_EVENT_RE.fullmatch(title.strip())
    return match.group("city") if match else None


def normalized_city(value: str) -> str:
    return " ".join(value.casefold().split())


def event_target_date(event: object) -> date | None:
    schedule = getattr(event, "schedule", None)
    return getattr(schedule, "event_date", None)


def levels_from_book(book: object) -> tuple[AskLevel, ...]:
    asks = getattr(book, "asks", ()) or ()
    levels = [
        AskLevel(price=Decimal(str(level.price)), size=Decimal(str(level.size)))
        for level in asks
        if Decimal(str(level.size)) > ZERO and ZERO < Decimal(str(level.price)) < ONE
    ]
    # The CLOB returns asks in descending order. Buy-side consumption starts
    # from the cheapest level, independent of the API's presentation order.
    return tuple(sorted(levels, key=lambda level: level.price))


def fill_leg(
    leg: BasketLeg,
    *,
    shares: int,
    fee_rate: Decimal,
) -> FilledLeg | None:
    remaining = Decimal(shares)
    raw_cost = ZERO
    fee_cost = ZERO
    limit_price: Decimal | None = None
    for level in leg.asks:
        take = min(remaining, level.size)
        if take <= ZERO:
            continue
        raw_cost += take * level.price
        fee_cost += take * fee_rate * level.price * (ONE - level.price)
        remaining -= take
        limit_price = level.price
        if remaining == ZERO:
            break
    if remaining != ZERO or limit_price is None:
        return None
    return FilledLeg(
        leg=leg,
        raw_cost=raw_cost,
        fee_cost=fee_cost,
        limit_price=limit_price,
    )


def quote_for_shares(
    *,
    event_slug: str,
    city: str,
    target_date: date,
    forecast_c: Decimal,
    legs: Iterable[BasketLeg],
    shares: int,
    fee_rate: Decimal,
) -> BasketQuote | None:
    if shares < 1:
        return None
    fills = [fill_leg(leg, shares=shares, fee_rate=fee_rate) for leg in legs]
    if any(item is None for item in fills):
        return None
    filled = tuple(item for item in fills if item is not None)
    return BasketQuote(
        event_slug=event_slug,
        city=city,
        target_date=target_date,
        forecast_c=forecast_c,
        shares=shares,
        legs=filled,
        raw_cost=sum((item.raw_cost for item in filled), start=ZERO),
        fee_cost=sum((item.fee_cost for item in filled), start=ZERO),
    )


def minimum_shares_for_raw_cost(leg: BasketLeg, minimum_cost: Decimal) -> int | None:
    maximum = floor_integer(sum(level.size for level in leg.asks))
    if maximum < 1:
        return None
    lower = 1
    upper = maximum
    result: int | None = None
    while lower <= upper:
        shares = (lower + upper) // 2
        filled = fill_leg(leg, shares=shares, fee_rate=ZERO)
        if filled is not None and filled.raw_cost >= minimum_cost:
            result = shares
            upper = shares - 1
        else:
            lower = shares + 1
    return result


def dynamic_basket_quote(
    *,
    event_slug: str,
    city: str,
    target_date: date,
    forecast_c: Decimal,
    legs: tuple[BasketLeg, ...],
    config: LiveTradingConfig,
) -> BasketQuote | None:
    if not legs:
        return None
    minimum_shares = max(ceil_integer(leg.min_order_size) for leg in legs)
    maximum_shares = min(floor_integer(sum(level.size for level in leg.asks)) for leg in legs)
    required_market_shares = [
        minimum_shares_for_raw_cost(leg, MIN_MARKETABLE_BUY_USD) for leg in legs
    ]
    if any(value is None for value in required_market_shares):
        return None
    minimum_shares = max(
        minimum_shares,
        *(value for value in required_market_shares if value is not None),
    )
    if maximum_shares < minimum_shares:
        return None

    best: BasketQuote | None = None
    lower = minimum_shares
    upper = maximum_shares
    while lower <= upper:
        shares = (lower + upper) // 2
        quote = quote_for_shares(
            event_slug=event_slug,
            city=city,
            target_date=target_date,
            forecast_c=forecast_c,
            legs=legs,
            shares=shares,
            fee_rate=config.fee_rate,
        )
        allowed = (
            quote is not None
            and quote.raw_cost_per_share <= config.max_raw_basket_cost
            and quote.all_in_cost <= config.max_basket_usd
        )
        if allowed:
            best = quote
            lower = shares + 1
        else:
            upper = shares - 1
    return best


def forecast_daily_high(city: str, target_date: date, config: OpenMeteoConfig) -> Decimal:
    geocoding_url = (
        f"{GEOCODING_ENDPOINT}?name={urllib.parse.quote_plus(city)}&count=1&language=en&format=json"
    )
    geocoding = fetch_json(geocoding_url)
    results = geocoding.get("results")
    if not isinstance(results, list) or not results:
        raise RuntimeError(f"Open-Meteo could not geocode weather market city {city!r}")
    location = results[0]
    timezone = location.get("timezone")
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    if timezone is None or latitude is None or longitude is None:
        raise RuntimeError(f"Open-Meteo returned incomplete geocoding for {city!r}")
    forecast_url = (
        f"{config.endpoint}?latitude={latitude}&longitude={longitude}"
        f"&daily=temperature_2m_max&timezone={timezone}"
        f"&start_date={target_date.isoformat()}&end_date={target_date.isoformat()}"
    )
    payload = fetch_json(forecast_url)
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise RuntimeError(f"Open-Meteo did not return a daily forecast for {city}")
    dates = daily.get("time")
    values = daily.get("temperature_2m_max")
    if not isinstance(dates, list) or not isinstance(values, list):
        raise RuntimeError(f"Open-Meteo did not return daily maximum temperature for {city}")
    for raw_date, value in zip(dates, values, strict=False):
        if raw_date == target_date.isoformat() and value is not None:
            return Decimal(str(value))
    raise RuntimeError(f"Open-Meteo did not return {target_date} for {city}")


async def scan_live_baskets(
    *,
    live_config: LiveTradingConfig,
    forecast_config: OpenMeteoConfig,
) -> list[BasketQuote]:
    today = date.today()
    latest_target = today + timedelta(days=2)
    quotes: list[BasketQuote] = []
    allowed_cities = {normalized_city(city) for city in forecast_config.cities}
    async with AsyncPublicClient() as client:
        events = []
        for offset in range((latest_target - today).days + 1):
            paginator = client.list_events(
                closed=False,
                event_date=today + timedelta(days=offset),
                tag_slug="weather",
                page_size=live_config.event_page_size,
            )
            page = await paginator.first_page()
            events.extend(page.items)
        selected_events = [
            (event, city)
            for event in events
            if (city := city_from_event(event)) is not None
            and normalized_city(city) in allowed_cities
        ]
        for event, city in selected_events[: live_config.max_events]:
            if not getattr(event.state, "active", False) or getattr(event.state, "closed", False):
                continue
            target_date = event_target_date(event)
            event_slug = getattr(event, "slug", None)
            if (
                target_date is None
                or target_date < today
                or target_date > latest_target
                or not event_slug
            ):
                continue
            try:
                forecast_c = await asyncio.to_thread(
                    forecast_daily_high,
                    city,
                    target_date,
                    forecast_config,
                )
            except RuntimeError:
                continue

            requested_buckets = nearby_buckets(forecast_c)
            markets_by_bucket = {
                bucket: market
                for market in event.markets
                if (bucket := exact_temperature_bucket(market)) is not None
                and getattr(market.state, "accepting_orders", False)
                and getattr(market.outcomes.yes, "token_id", None) is not None
            }
            if any(bucket not in markets_by_bucket for bucket in requested_buckets):
                continue

            selected = [markets_by_bucket[bucket] for bucket in requested_buckets]
            token_ids = [str(market.outcomes.yes.token_id) for market in selected]
            books = await client.get_order_books(token_ids=token_ids)
            books_by_token = {str(book.token_id): book for book in books}
            legs: list[BasketLeg] = []
            for bucket, market, token_id in zip(requested_buckets, selected, token_ids, strict=True):
                book = books_by_token.get(token_id)
                if book is None:
                    break
                asks = levels_from_book(book)
                if not asks:
                    break
                legs.append(
                    BasketLeg(
                        bucket_c=bucket,
                        token_id=token_id,
                        question=market.question or f"{city} {bucket}C",
                        asks=asks,
                        min_order_size=Decimal(str(book.min_order_size)),
                        tick_size=Decimal(str(book.tick_size)),
                    )
                )
            if len(legs) != len(requested_buckets):
                continue
            quote = dynamic_basket_quote(
                event_slug=str(event_slug),
                city=city,
                target_date=target_date,
                forecast_c=forecast_c,
                legs=tuple(legs),
                config=live_config,
            )
            if quote is not None:
                quotes.append(quote)
    return sorted(quotes, key=lambda quote: quote.all_in_cost_per_share)


async def execute_live_basket(quote: BasketQuote) -> tuple[str, ...]:
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    wallet = os.getenv("POLYMARKET_WALLET_ADDRESS")
    if not private_key:
        raise RuntimeError("POLYMARKET_PRIVATE_KEY is required for live execution")
    if os.getenv("WEATHER_BOT_ENABLE_LIVE") != "1":
        raise RuntimeError("Refusing live execution unless WEATHER_BOT_ENABLE_LIVE=1")

    async with await AsyncSecureClient.create(private_key=private_key, wallet=wallet) as client:
        if any(fill.raw_cost < MIN_MARKETABLE_BUY_USD for fill in quote.legs):
            raise RuntimeError("Each marketable BUY leg must have at least $1 of raw order value")
        signed_orders = await asyncio.gather(
            *(
                client.create_limit_order(
                    token_id=fill.leg.token_id,
                    side="BUY",
                    price=fill.limit_price,
                    size=quote.shares,
                )
                for fill in quote.legs
            )
        )
        responses = await client.post_orders(signed_orders)
        details: list[str] = []
        for fill, response in zip(quote.legs, responses, strict=True):
            if response.ok:
                if response.status == "live":
                    await client.cancel_order(order_id=response.order_id)
                    status = "accepted then cancelled (not immediately matched)"
                else:
                    status = response.status
                details.append(f"{fill.leg.bucket_c}C: {status}, order={response.order_id}")
            else:
                details.append(f"{fill.leg.bucket_c}C: rejected {response.code}: {response.message}")

        # CLOB batches submit together but are not atomic. When any leg fails,
        # a matched leg is no longer the intended temperature basket, so clear
        # it immediately rather than leaving directional temperature exposure.
        if not all(response.ok and response.status == "matched" for response in responses):
            for fill, response in zip(quote.legs, responses, strict=True):
                if not response.ok or response.status != "matched" or response.taking_amount <= ZERO:
                    continue
                book = await client.get_order_book(token_id=fill.leg.token_id)
                bids = sorted(book.bids, key=lambda level: level.price, reverse=True)
                if not bids:
                    details.append(f"{fill.leg.bucket_c}C: residual could not be flattened (no bid)")
                    continue
                flatten = await client.place_market_order(
                    token_id=fill.leg.token_id,
                    side="SELL",
                    shares=response.taking_amount,
                    min_price=bids[0].price,
                    order_type="FOK",
                )
                if flatten.ok:
                    details.append(f"{fill.leg.bucket_c}C: residual flattened, order={flatten.order_id}")
                else:
                    details.append(
                        f"{fill.leg.bucket_c}C: residual flatten rejected {flatten.code}: {flatten.message}"
                    )
        return tuple(details)
