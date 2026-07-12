from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from weather_polymarket_bot.config import AppConfig
from weather_polymarket_bot.backtest import (
    parse_date,
    previous_calendar_month,
    run_backtest,
    summarize,
)
from weather_polymarket_bot.models import BacktestResult, nearby_buckets, utc_now
from weather_polymarket_bot.open_meteo import fetch_open_meteo_round
from weather_polymarket_bot.parser import parse_forecasts
from weather_polymarket_bot.storage import ForecastStore
from weather_polymarket_bot.telegram_fetcher import fetch_weather_round


SAMPLE_MESSAGE = """
Los Angeles - 22.8C
Buenos Aires - 12.8C
London - 19.1C
Hong Kong - 29.6C
"""


def print_forecast(city: str, forecast_c: object, buckets: list[int]) -> None:
    bucket_text = "/".join(f"{bucket}C" for bucket in buckets)
    print(f"{city}: {forecast_c}C -> buy-basket candidates {bucket_text}")


def open_meteo_round(args: argparse.Namespace) -> int:
    config = AppConfig.from_env()
    open_meteo = config.open_meteo
    if args.days is not None:
        open_meteo = type(open_meteo)(
            cities=open_meteo.cities,
            forecast_days=args.days,
            daily_variable=open_meteo.daily_variable,
            endpoint=open_meteo.endpoint,
        )
    db_path = Path(args.db) if args.db else config.database_path
    forecasts = fetch_open_meteo_round(open_meteo)
    with ForecastStore(db_path) as store:
        ids = store.insert_many(forecasts)
    print(f"Saved {len(ids)} Open-Meteo forecast observation(s) to {db_path}")
    for forecast in forecasts:
        label = f" {forecast.target_label}" if forecast.target_label else ""
        print_forecast(f"{forecast.city}{label}", forecast.forecast_c, forecast.buckets_c)
    return 0 if forecasts else 2


async def telegram_round(args: argparse.Namespace) -> int:
    config = AppConfig.from_env()
    if args.db:
        config = AppConfig(
            database_path=Path(args.db),
            open_meteo=config.open_meteo,
            backtest=config.backtest,
            telegram=config.telegram,
        )
    replies = await fetch_weather_round(config.telegram)
    forecasts = []
    for reply in replies:
        parsed = parse_forecasts(
            reply.text,
            fetched_at=utc_now(),
            message_id=reply.message_id,
        )
        if not parsed:
            print(f"No forecast parsed for command {reply.command!r}. Reply was:")
            print(reply.text)
        forecasts.extend(parsed)
    with ForecastStore(config.database_path) as store:
        ids = store.insert_many(forecasts)
    print(f"Saved {len(ids)} forecast observation(s) to {config.database_path}")
    for forecast in forecasts:
        print_forecast(forecast.city, forecast.forecast_c, forecast.buckets_c)
    return 0 if forecasts else 2


def parse_sample(args: argparse.Namespace) -> int:
    text = args.text or SAMPLE_MESSAGE
    forecasts = parse_forecasts(text, fetched_at=utc_now())
    if args.save:
        config = AppConfig.from_env()
        db_path = Path(args.db) if args.db else config.database_path
        with ForecastStore(db_path) as store:
            ids = store.insert_many(forecasts)
        print(f"Saved {len(ids)} sample forecast observation(s) to {db_path}")
    for forecast in forecasts:
        print_forecast(forecast.city, forecast.forecast_c, forecast.buckets_c)
    return 0 if forecasts else 2


def show_recent(args: argparse.Namespace) -> int:
    config = AppConfig.from_env()
    db_path = Path(args.db) if args.db else config.database_path
    with ForecastStore(db_path) as store:
        rows = store.recent(limit=args.limit)
    for row in rows:
        print(
            f"#{row['id']} {row['city']} {row['forecast_c']}C "
            f"center={row['center_bucket_c']}C basket={row['bucket_low_c']}..{row['bucket_high_c']}C "
            f"target={row['target_label']} fetched_at={row['fetched_at']}"
        )
    if not rows:
        print(f"No rows in {db_path}")
    return 0


def bucket(args: argparse.Namespace) -> int:
    forecast_c = Decimal(args.forecast_c)
    buckets = nearby_buckets(forecast_c, radius=args.radius)
    print_forecast("forecast", forecast_c, buckets)
    return 0


def print_backtest_summary(results: list[BacktestResult]) -> None:
    summary = summarize(results)
    print(f"Basket wins: {summary.wins}/{summary.total} ({summary.hit_rate:.1%})")
    print(f"Empirical fair basket cost before fees: {summary.fair_basket_cost_cents:.1f}c")

    city_results: dict[str, list[BacktestResult]] = defaultdict(list)
    for result in results:
        city_results[result.city].append(result)
    for city, city_rows in sorted(city_results.items()):
        city_summary = summarize(city_rows)
        print(
            f"{city}: {city_summary.wins}/{city_summary.total} "
            f"({city_summary.hit_rate:.1%}), fair cost {city_summary.fair_basket_cost_cents:.1f}c"
        )


def backtest_month(args: argparse.Namespace) -> int:
    config = AppConfig.from_env()
    start_date, end_date = previous_calendar_month(date.today())
    if args.start:
        start_date = parse_date(args.start)
    if args.end:
        end_date = parse_date(args.end)

    results = run_backtest(
        config=config.backtest,
        start_date=start_date,
        end_date=end_date,
    )
    db_path = Path(args.db) if args.db else config.database_path
    with ForecastStore(db_path) as store:
        run_id = store.create_backtest_run(
            source="open-meteo:single-run+archive",
            model=config.backtest.model,
            start_date=start_date,
            end_date=end_date,
            run_hour_utc=config.backtest.run_hour_utc,
        )
        stored = store.insert_backtest_results(run_id=run_id, results=results)

    print(
        f"Saved backtest #{run_id}: {stored} city-day basket(s), "
        f"{start_date.isoformat()} through {end_date.isoformat()}"
    )
    print(
        f"Forecast: {config.backtest.model}, issued at {config.backtest.run_hour_utc:02d}:00 UTC "
        "on the preceding day; outcome: Open-Meteo archive daily maximum."
    )
    print_backtest_summary(results)
    fallback_count = sum(
        result.issued_at.hour != config.backtest.run_hour_utc for result in results
    )
    if fallback_count:
        print(f"Used the same-day 00:00 UTC archive fallback for {fallback_count} unavailable 12:00 UTC run(s).")
    if args.verbose:
        for result in results:
            status = "WIN" if result.won else "LOSS"
            buckets = "/".join(f"{bucket}C" for bucket in result.buckets_c)
            print(
                f"{status} {result.city} {result.target_date}: forecast {result.forecast_c}C "
                f"-> {buckets}; outcome {result.outcome_c}C ({result.outcome_bucket_c}C)"
            )
    print("This is forecast-skill only; PnL needs historical Polymarket asks and fees.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weather forecast Polymarket backtest tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    open_meteo = subparsers.add_parser("open-meteo-round", help="Fetch one forecast round from Open-Meteo")
    open_meteo.add_argument("--db", help="SQLite database path")
    open_meteo.add_argument("--days", type=int, help="Forecast days to request")
    open_meteo.set_defaults(func=open_meteo_round)

    telegram = subparsers.add_parser("telegram-round", help="Fetch one forecast round from @weatherscan_bot")
    telegram.add_argument("--db", help="SQLite database path")
    telegram.set_defaults(func=lambda args: asyncio.run(telegram_round(args)))

    sample = subparsers.add_parser("parse-sample", help="Parse a sample or pasted weather message")
    sample.add_argument("--text", help="Message text to parse. Defaults to a built-in sample.")
    sample.add_argument("--save", action="store_true", help="Save parsed sample rows to SQLite")
    sample.add_argument("--db", help="SQLite database path")
    sample.set_defaults(func=parse_sample)

    recent = subparsers.add_parser("recent", help="Show recent stored forecast observations")
    recent.add_argument("--db", help="SQLite database path")
    recent.add_argument("--limit", type=int, default=20)
    recent.set_defaults(func=show_recent)

    bucket_parser = subparsers.add_parser("bucket", help="Show nearby Celsius basket buckets")
    bucket_parser.add_argument("forecast_c")
    bucket_parser.add_argument("--radius", type=int, default=1)
    bucket_parser.set_defaults(func=bucket)

    backtest = subparsers.add_parser(
        "backtest-month",
        help="Backtest the nearby-bucket rule for the prior calendar month",
    )
    backtest.add_argument("--start", help="Override start date (YYYY-MM-DD)")
    backtest.add_argument("--end", help="Override end date (YYYY-MM-DD)")
    backtest.add_argument("--db", help="SQLite database path")
    backtest.add_argument("--verbose", action="store_true", help="Print every city-day result")
    backtest.set_defaults(func=backtest_month)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (RuntimeError, TimeoutError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
