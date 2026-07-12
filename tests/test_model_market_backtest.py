from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from weather_polymarket_bot.model_market_backtest import select_model_neighbor_markets


def market(label: str) -> SimpleNamespace:
    return SimpleNamespace(group_item_title=label)


def test_model_selection_uses_the_forecast_bucket_and_its_neighbors_in_celsius() -> None:
    selected = select_model_neighbor_markets(
        [
            market("18\u00b0C or below"),
            market("19\u00b0C"),
            market("20\u00b0C"),
            market("21\u00b0C"),
            market("22\u00b0C"),
            market("23\u00b0C or higher"),
        ],
        forecast_c=Decimal("21.2"),
    )

    assert selected is not None
    assert [item.group_item_title for item in selected] == ["20\u00b0C", "21\u00b0C", "22\u00b0C"]


def test_model_selection_converts_celsius_forecast_for_fahrenheit_market_buckets() -> None:
    selected = select_model_neighbor_markets(
        [
            market("66-67\u00b0F or below"),
            market("68-69\u00b0F"),
            market("70-71\u00b0F"),
            market("72-73\u00b0F"),
            market("74\u00b0F or higher"),
        ],
        forecast_c=Decimal("21.2"),
    )

    assert selected is not None
    assert [item.group_item_title for item in selected] == ["68-69\u00b0F", "70-71\u00b0F", "72-73\u00b0F"]
