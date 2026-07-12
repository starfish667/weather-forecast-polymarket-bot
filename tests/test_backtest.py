from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from weather_polymarket_bot.backtest import (
    archive_daily_maxima_from_payload,
    build_single_run_url,
    forecast_daily_max_from_payload,
    previous_calendar_month,
    run_time_for_target,
    summarize,
)
from weather_polymarket_bot.config import BacktestConfig
from weather_polymarket_bot.models import BacktestResult
from weather_polymarket_bot.open_meteo import location_for_city


def test_previous_calendar_month() -> None:
    assert previous_calendar_month(date(2026, 7, 12)) == (date(2026, 6, 1), date(2026, 6, 30))
    assert previous_calendar_month(date(2026, 1, 3)) == (date(2025, 12, 1), date(2025, 12, 31))


def test_build_single_run_url_uses_model_and_prior_day_run() -> None:
    config = BacktestConfig(
        cities=["London"],
        model="ecmwf_ifs",
        run_hour_utc=12,
        workers=1,
        single_run_endpoint="https://single-runs-api.open-meteo.com/v1/forecast",
        archive_endpoint="https://archive-api.open-meteo.com/v1/archive",
    )
    run_at = run_time_for_target(date(2026, 6, 2), run_hour_utc=12)

    url = build_single_run_url(
        config=config,
        location=location_for_city("London"),
        run_at=run_at,
    )

    assert "run=2026-06-01T12%3A00" in url
    assert "models=ecmwf_ifs" in url
    assert "hourly=temperature_2m" in url


def test_extracts_daily_forecast_maximum_and_archive_outcome() -> None:
    payload = {
        "hourly": {
            "time": [f"2026-06-02T{hour:02d}:00" for hour in range(24)],
            "temperature_2m": [10 + hour / 10 for hour in range(24)],
        }
    }
    outcomes = archive_daily_maxima_from_payload(
        {"daily": {"time": ["2026-06-02"], "temperature_2m_max": [12.8]}}
    )

    assert forecast_daily_max_from_payload(payload, target_date=date(2026, 6, 2)) == Decimal("12.3")
    assert outcomes == {date(2026, 6, 2): Decimal("12.8")}


def test_summary_uses_basket_hit_rate_as_fee_free_fair_cost() -> None:
    issued_at = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    won = BacktestResult(
        city="London",
        target_date=date(2026, 6, 2),
        issued_at=issued_at,
        forecast_c=Decimal("12.8"),
        outcome_c=Decimal("13.1"),
        source="test",
        model="test",
    )
    lost = BacktestResult(
        city="London",
        target_date=date(2026, 6, 3),
        issued_at=issued_at,
        forecast_c=Decimal("12.8"),
        outcome_c=Decimal("16.1"),
        source="test",
        model="test",
    )

    summary = summarize([won, lost])

    assert summary.wins == 1
    assert summary.total == 2
    assert summary.hit_rate == Decimal("0.5")
    assert summary.fair_basket_cost_cents == Decimal("50.0")
