from __future__ import annotations

from datetime import date
from decimal import Decimal

from weather_polymarket_bot.config import LiveTradingConfig
from weather_polymarket_bot.live_trading import AskLevel, BasketLeg, dynamic_basket_quote


def live_config(*, max_cost: str = "0.80", max_usd: str = "5") -> LiveTradingConfig:
    return LiveTradingConfig(
        max_raw_basket_cost=Decimal(max_cost),
        max_basket_usd=Decimal(max_usd),
        fee_rate=Decimal("0.05"),
        event_page_size=20,
        max_events=10,
        max_baskets_per_round=1,
    )


def leg(bucket: int, price: str, *, size: str = "10", minimum: str = "5") -> BasketLeg:
    return BasketLeg(
        bucket_c=bucket,
        token_id=str(bucket),
        question=f"{bucket}C",
        asks=(AskLevel(price=Decimal(price), size=Decimal(size)),),
        min_order_size=Decimal(minimum),
        tick_size=Decimal("0.001"),
    )


def test_dynamic_quote_uses_same_share_count_and_respects_total_budget() -> None:
    quote = dynamic_basket_quote(
        event_slug="example",
        city="Example",
        target_date=date(2026, 7, 12),
        forecast_c=Decimal("23.2"),
        legs=(leg(22, "0.20"), leg(23, "0.35"), leg(24, "0.25")),
        config=live_config(),
    )

    assert quote is not None
    assert quote.shares == 6
    assert quote.raw_cost_per_share == Decimal("0.80")
    assert quote.all_in_cost <= Decimal("5")
    assert [fill.leg.bucket_c for fill in quote.legs] == [22, 23, 24]


def test_dynamic_quote_rejects_a_basket_above_the_raw_80_cent_limit() -> None:
    quote = dynamic_basket_quote(
        event_slug="example",
        city="Example",
        target_date=date(2026, 7, 12),
        forecast_c=Decimal("23.2"),
        legs=(leg(22, "0.20"), leg(23, "0.30"), leg(24, "0.40")),
        config=live_config(),
    )

    assert quote is None


def test_dynamic_quote_skips_when_each_leg_cannot_reach_the_one_dollar_buy_minimum() -> None:
    quote = dynamic_basket_quote(
        event_slug="example",
        city="Example",
        target_date=date(2026, 7, 12),
        forecast_c=Decimal("23.2"),
        legs=(leg(22, "0.58"), leg(23, "0.13"), leg(24, "0.009", size="500")),
        config=live_config(max_usd="5"),
    )

    assert quote is None
