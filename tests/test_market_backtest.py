from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from weather_polymarket_bot.market_backtest import (
    HistoricalLeg,
    basket_from_legs,
    latest_price_at_or_before,
    summarize_threshold,
)


def test_historical_basket_selects_the_three_highest_prices_and_includes_weather_fees() -> None:
    basket = basket_from_legs(
        event_slug="example",
        city="Example",
        target_date=date(2026, 6, 1),
        entered_at=datetime(2026, 5, 31, 12, tzinfo=timezone.utc),
        legs=(
            HistoricalLeg("21C", Decimal("0.40"), False, True),
            HistoricalLeg("22C", Decimal("0.30"), True, True),
            HistoricalLeg("23C", Decimal("0.12"), False, True),
            HistoricalLeg("24C", Decimal("0.10"), False, True),
        ),
    )

    assert basket is not None
    assert basket.labels == "21C/22C/23C"
    assert basket.raw_cost == Decimal("0.82")
    assert basket.fee_cost == Decimal("0.02778")
    assert basket.payout == Decimal("1")
    assert basket.pnl == Decimal("0.15222")

    at_80 = summarize_threshold([basket], raw_threshold=Decimal("0.80"))
    at_85 = summarize_threshold([basket], raw_threshold=Decimal("0.85"))
    assert at_80.entries == 0
    assert at_85.entries == 1
    assert at_85.pnl == Decimal("0.15222")


def test_latest_price_uses_the_last_valid_mark_before_entry() -> None:
    points = (
        SimpleNamespace(t=100, p=0.21),
        SimpleNamespace(t=110, p=0.34),
        SimpleNamespace(t=120, p=0.55),
    )

    assert latest_price_at_or_before(points, entry_ts=115) == Decimal("0.34")
    assert latest_price_at_or_before(points, entry_ts=99) is None
