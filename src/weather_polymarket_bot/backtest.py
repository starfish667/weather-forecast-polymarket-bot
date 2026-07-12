from __future__ import annotations

import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from weather_polymarket_bot.config import BacktestConfig
from weather_polymarket_bot.models import BacktestResult
from weather_polymarket_bot.open_meteo import (
    CityLocation,
    OpenMeteoHTTPError,
    fetch_json,
    location_for_city,
)


TEMPERATURE_VARIABLE = "temperature_2m"
DAILY_MAX_VARIABLE = "temperature_2m_max"


@dataclass(frozen=True)
class BacktestSummary:
    total: int
    wins: int

    @property
    def hit_rate(self) -> Decimal:
        if not self.total:
            return Decimal("0")
        return Decimal(self.wins) / Decimal(self.total)

    @property
    def fair_basket_cost_cents(self) -> Decimal:
        """Maximum all-in basket cost before fees at the observed hit rate."""
        return self.hit_rate * Decimal("100")


def previous_calendar_month(today: date) -> tuple[date, date]:
    first_this_month = today.replace(day=1)
    end = first_this_month - timedelta(days=1)
    return end.replace(day=1), end


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise RuntimeError(f"Expected YYYY-MM-DD date, got {value!r}") from error


def run_time_for_target(target_date: date, *, run_hour_utc: int) -> datetime:
    return datetime.combine(
        target_date - timedelta(days=1),
        time(hour=run_hour_utc),
        tzinfo=timezone.utc,
    )


def build_single_run_url(
    *,
    config: BacktestConfig,
    location: CityLocation,
    run_at: datetime,
) -> str:
    if run_at.tzinfo is None:
        raise ValueError("run_at must include a timezone")
    run_utc = run_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    params = {
        "latitude": str(location.latitude),
        "longitude": str(location.longitude),
        "run": run_utc,
        "hourly": TEMPERATURE_VARIABLE,
        "models": config.model,
        "timezone": location.timezone,
    }
    return f"{config.single_run_endpoint}?{urllib.parse.urlencode(params)}"


def build_archive_url(
    *,
    config: BacktestConfig,
    location: CityLocation,
    start_date: date,
    end_date: date,
) -> str:
    params = {
        "latitude": str(location.latitude),
        "longitude": str(location.longitude),
        "daily": DAILY_MAX_VARIABLE,
        "timezone": location.timezone,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    return f"{config.archive_endpoint}?{urllib.parse.urlencode(params)}"


def forecast_daily_max_from_payload(payload: dict[str, Any], *, target_date: date) -> Decimal:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise RuntimeError("Single Runs response did not contain an hourly block")
    timestamps = hourly.get("time")
    values = hourly.get(TEMPERATURE_VARIABLE)
    if not isinstance(timestamps, list) or not isinstance(values, list):
        raise RuntimeError("Single Runs response did not contain hourly temperature_2m")

    target_prefix = target_date.isoformat()
    temperatures = [
        Decimal(str(value))
        for timestamp, value in zip(timestamps, values, strict=False)
        if isinstance(timestamp, str) and timestamp.startswith(target_prefix) and value is not None
    ]
    # A local daylight-saving day has 23 hours. Fewer means this run does not
    # fully cover the target day and would bias a daily maximum downward.
    if len(temperatures) < 23:
        raise RuntimeError(
            f"Single Runs forecast contains only {len(temperatures)} hourly values for {target_date}"
        )
    return max(temperatures)


def archive_daily_maxima_from_payload(payload: dict[str, Any]) -> dict[date, Decimal]:
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise RuntimeError("Archive response did not contain a daily block")
    dates = daily.get("time")
    values = daily.get(DAILY_MAX_VARIABLE)
    if not isinstance(dates, list) or not isinstance(values, list):
        raise RuntimeError("Archive response did not contain daily temperature_2m_max")

    outcomes: dict[date, Decimal] = {}
    for raw_date, value in zip(dates, values, strict=False):
        if value is not None:
            outcomes[parse_date(str(raw_date))] = Decimal(str(value))
    return outcomes


def fetch_city_outcomes(
    *,
    config: BacktestConfig,
    location: CityLocation,
    start_date: date,
    end_date: date,
) -> dict[date, Decimal]:
    payload = fetch_json(
        build_archive_url(
            config=config,
            location=location,
            start_date=start_date,
            end_date=end_date,
        )
    )
    outcomes = archive_daily_maxima_from_payload(payload)
    expected = {
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    }
    missing = sorted(expected - outcomes.keys())
    if missing:
        labels = ", ".join(item.isoformat() for item in missing[:3])
        raise RuntimeError(f"Archive data is missing {location.name} date(s): {labels}")
    return outcomes


def _fetch_result(
    *,
    config: BacktestConfig,
    location: CityLocation,
    target_date: date,
    outcome_c: Decimal,
) -> BacktestResult:
    issued_at = run_time_for_target(target_date, run_hour_utc=config.run_hour_utc)
    fallback_run = issued_at.replace(hour=0)
    run_times = [issued_at]
    if fallback_run != issued_at:
        run_times.append(fallback_run)

    last_error: RuntimeError | None = None
    for run_at in run_times:
        try:
            payload = fetch_json(
                build_single_run_url(config=config, location=location, run_at=run_at)
            )
        except OpenMeteoHTTPError as error:
            # Some archived ECMWF cycles are unavailable. The same-day 00Z
            # cycle still predates and fully covers the following local day.
            if error.status_code == 400 and run_at != fallback_run:
                last_error = error
                continue
            raise RuntimeError(
                f"{location.name} target {target_date} run {run_at:%Y-%m-%dT%H:%MZ}: {error}"
            ) from error
        except RuntimeError as error:
            raise RuntimeError(
                f"{location.name} target {target_date} run {run_at:%Y-%m-%dT%H:%MZ}: {error}"
            ) from error
        return BacktestResult(
            city=location.name,
            target_date=target_date,
            issued_at=run_at,
            forecast_c=forecast_daily_max_from_payload(payload, target_date=target_date),
            outcome_c=outcome_c,
            source="open-meteo:archive",
            model=config.model,
        )

    raise RuntimeError(
        f"{location.name} target {target_date} has no usable archived forecast run: {last_error}"
    )


def run_backtest(
    *,
    config: BacktestConfig,
    start_date: date,
    end_date: date,
) -> list[BacktestResult]:
    if end_date < start_date:
        raise RuntimeError("Backtest end date must be on or after the start date")

    locations = [location_for_city(city) for city in config.cities]
    outcomes = {
        location.name: fetch_city_outcomes(
            config=config,
            location=location,
            start_date=start_date,
            end_date=end_date,
        )
        for location in locations
    }
    target_dates = [
        start_date + timedelta(days=offset)
        for offset in range((end_date - start_date).days + 1)
    ]

    futures = []
    results: list[BacktestResult] = []
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=config.workers) as executor:
        for location in locations:
            for target_date in target_dates:
                futures.append(
                    executor.submit(
                        _fetch_result,
                        config=config,
                        location=location,
                        target_date=target_date,
                        outcome_c=outcomes[location.name][target_date],
                    )
                )
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except (RuntimeError, TimeoutError, ValueError) as error:
                failures.append(str(error))

    if failures:
        examples = "; ".join(failures[:3])
        raise RuntimeError(f"Backtest failed for {len(failures)} forecast run(s): {examples}")
    return sorted(results, key=lambda item: (item.target_date, item.city))


def summarize(results: list[BacktestResult]) -> BacktestSummary:
    return BacktestSummary(total=len(results), wins=sum(result.won for result in results))
