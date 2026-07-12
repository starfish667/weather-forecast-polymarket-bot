from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

from weather_polymarket_bot.models import BacktestResult, ForecastObservation


SCHEMA = """
CREATE TABLE IF NOT EXISTS forecast_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    city TEXT NOT NULL,
    forecast_c TEXT NOT NULL,
    center_bucket_c INTEGER NOT NULL,
    bucket_radius INTEGER NOT NULL,
    bucket_low_c INTEGER NOT NULL,
    bucket_high_c INTEGER NOT NULL,
    raw_text TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    issued_at TEXT,
    message_id INTEGER,
    target_label TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_forecasts_city_fetched
ON forecast_observations(city, fetched_at);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    model TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    run_hour_utc INTEGER NOT NULL,
    bucket_radius INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES backtest_runs(id),
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    forecast_c TEXT NOT NULL,
    forecast_bucket_c INTEGER NOT NULL,
    outcome_c TEXT NOT NULL,
    outcome_bucket_c INTEGER NOT NULL,
    bucket_low_c INTEGER NOT NULL,
    bucket_high_c INTEGER NOT NULL,
    won INTEGER NOT NULL CHECK (won IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_backtest_results_run
ON backtest_results(run_id, city, target_date);
"""


def encode_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def encode_date(value: date) -> str:
    return value.isoformat()


class ForecastStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.init_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ForecastStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def init_schema(self) -> None:
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def insert_forecast(self, forecast: ForecastObservation) -> int:
        buckets = forecast.buckets_c
        cursor = self.connection.execute(
            """
            INSERT INTO forecast_observations (
                source, city, forecast_c, center_bucket_c, bucket_radius,
                bucket_low_c, bucket_high_c, raw_text, fetched_at, issued_at,
                message_id, target_label
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                forecast.source,
                forecast.city,
                str(forecast.forecast_c),
                forecast.center_bucket_c,
                forecast.bucket_radius,
                buckets[0],
                buckets[-1],
                forecast.raw_text,
                encode_dt(forecast.fetched_at),
                encode_dt(forecast.issued_at),
                forecast.message_id,
                forecast.target_label,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def insert_many(self, forecasts: Iterable[ForecastObservation]) -> list[int]:
        return [self.insert_forecast(forecast) for forecast in forecasts]

    def recent(self, limit: int = 20) -> list[sqlite3.Row]:
        cursor = self.connection.execute(
            """
            SELECT id, source, city, forecast_c, center_bucket_c, bucket_low_c,
                   bucket_high_c, fetched_at, target_label, message_id
            FROM forecast_observations
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return list(cursor.fetchall())

    def create_backtest_run(
        self,
        *,
        source: str,
        model: str,
        start_date: date,
        end_date: date,
        run_hour_utc: int,
        bucket_radius: int = 1,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO backtest_runs (
                source, model, start_date, end_date, run_hour_utc, bucket_radius
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                model,
                encode_date(start_date),
                encode_date(end_date),
                run_hour_utc,
                bucket_radius,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def insert_backtest_results(
        self,
        *,
        run_id: int,
        results: Iterable[BacktestResult],
    ) -> int:
        rows = []
        for result in results:
            buckets = result.buckets_c
            rows.append(
                (
                    run_id,
                    result.city,
                    encode_date(result.target_date),
                    encode_dt(result.issued_at),
                    str(result.forecast_c),
                    result.forecast_bucket_c,
                    str(result.outcome_c),
                    result.outcome_bucket_c,
                    buckets[0],
                    buckets[-1],
                    int(result.won),
                )
            )
        self.connection.executemany(
            """
            INSERT INTO backtest_results (
                run_id, city, target_date, issued_at, forecast_c,
                forecast_bucket_c, outcome_c, outcome_bucket_c,
                bucket_low_c, bucket_high_c, won
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.connection.commit()
        return len(rows)
