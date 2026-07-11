from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal
from pathlib import Path

from weather_polymarket_bot.config import AppConfig
from weather_polymarket_bot.models import nearby_buckets, utc_now
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


async def telegram_round(args: argparse.Namespace) -> int:
    config = AppConfig.from_env()
    if args.db:
        config = AppConfig(database_path=Path(args.db), telegram=config.telegram)
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
            f"fetched_at={row['fetched_at']}"
        )
    if not rows:
        print(f"No rows in {db_path}")
    return 0


def bucket(args: argparse.Namespace) -> int:
    forecast_c = Decimal(args.forecast_c)
    buckets = nearby_buckets(forecast_c, radius=args.radius)
    print_forecast("forecast", forecast_c, buckets)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Weather forecast Polymarket backtest tools.")
    subparsers = parser.add_subparsers(dest="command", required=True)

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
