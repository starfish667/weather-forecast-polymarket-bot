from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from polymarket import AsyncPublicClient

from weather_polymarket_bot.live_trading import city_from_event, event_target_date, normalized_city


ZERO = Decimal("0")
ONE = Decimal("1")
DEFAULT_RAW_THRESHOLDS = (Decimal("0.80"), Decimal("0.85"), Decimal("0.90"))


@dataclass(frozen=True)
class HistoricalLeg:
    label: str
    price: Decimal
    resolved_yes: bool
    fees_enabled: bool


@dataclass(frozen=True)
class HistoricalBasket:
    event_slug: str
    city: str
    target_date: date
    entered_at: datetime
    legs: tuple[HistoricalLeg, ...]

    @property
    def raw_cost(self) -> Decimal:
        return sum((leg.price for leg in self.legs), start=ZERO)

    @property
    def fee_cost(self) -> Decimal:
        return sum(
            (
                Decimal("0.05") * leg.price * (ONE - leg.price)
                for leg in self.legs
                if leg.fees_enabled
            ),
            start=ZERO,
        )

    @property
    def all_in_cost(self) -> Decimal:
        return self.raw_cost + self.fee_cost

    @property
    def payout(self) -> Decimal:
        return sum((ONE for leg in self.legs if leg.resolved_yes), start=ZERO)

    @property
    def pnl(self) -> Decimal:
        return self.payout - self.all_in_cost

    @property
    def labels(self) -> str:
        return "/".join(leg.label for leg in self.legs)


@dataclass(frozen=True)
class ThresholdSummary:
    raw_threshold: Decimal
    entries: int
    wins: int
    raw_cost: Decimal
    fee_cost: Decimal
    payout: Decimal

    @property
    def all_in_cost(self) -> Decimal:
        return self.raw_cost + self.fee_cost

    @property
    def pnl(self) -> Decimal:
        return self.payout - self.all_in_cost

    @property
    def hit_rate(self) -> Decimal:
        return Decimal(self.wins) / Decimal(self.entries) if self.entries else ZERO

    @property
    def roi(self) -> Decimal:
        return self.pnl / self.all_in_cost if self.all_in_cost else ZERO


@dataclass(frozen=True)
class MarketBacktestRun:
    matched_events: int
    skipped_events: int
    baskets: tuple[HistoricalBasket, ...]


def entry_time_for_target(target_date: date, *, entry_hour_utc: int) -> datetime:
    return datetime.combine(
        target_date - timedelta(days=1),
        time(hour=entry_hour_utc),
        tzinfo=timezone.utc,
    )


def latest_price_at_or_before(
    points: Iterable[object],
    *,
    entry_ts: int,
) -> Decimal | None:
    available = [
        (int(getattr(point, "t")), Decimal(str(getattr(point, "p"))))
        for point in points
        if int(getattr(point, "t")) <= entry_ts
        and ZERO < Decimal(str(getattr(point, "p"))) < ONE
    ]
    if not available:
        return None
    return max(available, key=lambda item: item[0])[1]


def basket_from_legs(
    *,
    event_slug: str,
    city: str,
    target_date: date,
    entered_at: datetime,
    legs: Iterable[HistoricalLeg],
) -> HistoricalBasket | None:
    ranked = sorted(legs, key=lambda leg: (-leg.price, leg.label))
    if len(ranked) < 3:
        return None
    return HistoricalBasket(
        event_slug=event_slug,
        city=city,
        target_date=target_date,
        entered_at=entered_at,
        legs=tuple(ranked[:3]),
    )


def summarize_threshold(
    baskets: Iterable[HistoricalBasket],
    *,
    raw_threshold: Decimal,
) -> ThresholdSummary:
    selected = [basket for basket in baskets if basket.raw_cost <= raw_threshold]
    return ThresholdSummary(
        raw_threshold=raw_threshold,
        entries=len(selected),
        wins=sum(basket.payout > ZERO for basket in selected),
        raw_cost=sum((basket.raw_cost for basket in selected), start=ZERO),
        fee_cost=sum((basket.fee_cost for basket in selected), start=ZERO),
        payout=sum((basket.payout for basket in selected), start=ZERO),
    )


async def historical_price_for_token(
    *,
    client: AsyncPublicClient,
    token_id: str,
    entered_at: datetime,
    lookback_hours: int,
    semaphore: asyncio.Semaphore,
) -> Decimal | None:
    entry_ts = int(entered_at.timestamp())
    async with semaphore:
        history = await client.get_price_history(
            token_id=token_id,
            start_ts=entry_ts - lookback_hours * 60 * 60,
            end_ts=entry_ts,
            fidelity=60,
        )
    return latest_price_at_or_before(history, entry_ts=entry_ts)


async def historical_basket_for_event(
    *,
    client: AsyncPublicClient,
    event: object,
    city: str,
    target_date: date,
    lookback_hours: int,
    semaphore: asyncio.Semaphore,
    entry_hour_utc: int,
) -> HistoricalBasket | None:
    event_slug = getattr(event, "slug", None)
    if not event_slug:
        return None
    entered_at = entry_time_for_target(target_date, entry_hour_utc=entry_hour_utc)
    markets = tuple(getattr(event, "markets", ()) or ())
    token_ids = [getattr(market.outcomes.yes, "token_id", None) for market in markets]
    if not markets or any(token_id is None for token_id in token_ids):
        return None

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
    for market, price in zip(markets, prices, strict=True):
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
    return basket_from_legs(
        event_slug=str(event_slug),
        city=city,
        target_date=target_date,
        entered_at=entered_at,
        legs=legs,
    )


async def run_market_backtest(
    *,
    cities: Iterable[str],
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

    allowed_cities = {normalized_city(city) for city in cities}
    candidates: list[tuple[object, str, date]] = []
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
                if (
                    city is not None
                    and normalized_city(city) in allowed_cities
                    and target_date == requested_date
                ):
                    candidates.append((event, city, target_date))

        semaphore = asyncio.Semaphore(concurrency)
        results = await asyncio.gather(
            *(
                historical_basket_for_event(
                    client=client,
                    event=event,
                    city=city,
                    target_date=target_date,
                    lookback_hours=lookback_hours,
                    semaphore=semaphore,
                    entry_hour_utc=entry_hour_utc,
                )
                for event, city, target_date in candidates
            ),
            return_exceptions=True,
        )

    baskets = [result for result in results if isinstance(result, HistoricalBasket)]
    return MarketBacktestRun(
        matched_events=len(candidates),
        skipped_events=len(candidates) - len(baskets),
        baskets=tuple(sorted(baskets, key=lambda basket: (basket.target_date, basket.city))),
    )
