from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def celsius_bucket(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def nearby_buckets(value: Decimal, radius: int = 1) -> list[int]:
    center = celsius_bucket(value)
    return list(range(center - radius, center + radius + 1))


@dataclass(frozen=True)
class ForecastObservation:
    source: str
    city: str
    forecast_c: Decimal
    raw_text: str
    fetched_at: datetime
    message_id: int | None = None
    target_label: str | None = None
    issued_at: datetime | None = None
    bucket_radius: int = 1

    @property
    def center_bucket_c(self) -> int:
        return celsius_bucket(self.forecast_c)

    @property
    def buckets_c(self) -> list[int]:
        return nearby_buckets(self.forecast_c, radius=self.bucket_radius)


@dataclass(frozen=True)
class BacktestResult:
    city: str
    target_date: date
    issued_at: datetime
    forecast_c: Decimal
    outcome_c: Decimal
    source: str
    model: str
    bucket_radius: int = 1

    @property
    def forecast_bucket_c(self) -> int:
        return celsius_bucket(self.forecast_c)

    @property
    def outcome_bucket_c(self) -> int:
        return celsius_bucket(self.outcome_c)

    @property
    def buckets_c(self) -> list[int]:
        return nearby_buckets(self.forecast_c, radius=self.bucket_radius)

    @property
    def won(self) -> bool:
        return self.outcome_bucket_c in self.buckets_c
