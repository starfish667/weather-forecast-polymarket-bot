from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CITIES = ("Los Angeles", "Buenos Aires", "London", "Hong Kong")


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def csv_list(value: str | None, default: tuple[str, ...]) -> list[str]:
    if not value:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class OpenMeteoConfig:
    cities: list[str]
    forecast_days: int
    daily_variable: str
    endpoint: str

    @classmethod
    def from_env(cls) -> "OpenMeteoConfig":
        load_dotenv()
        return cls(
            cities=csv_list(os.getenv("FORECAST_CITIES"), DEFAULT_CITIES),
            forecast_days=int(os.getenv("OPEN_METEO_FORECAST_DAYS", "3")),
            daily_variable=os.getenv("OPEN_METEO_DAILY_VARIABLE", "temperature_2m_max"),
            endpoint=os.getenv("OPEN_METEO_ENDPOINT", "https://api.open-meteo.com/v1/forecast"),
        )


@dataclass(frozen=True)
class BacktestConfig:
    cities: list[str]
    model: str
    run_hour_utc: int
    workers: int
    single_run_endpoint: str
    archive_endpoint: str

    @classmethod
    def from_env(cls) -> "BacktestConfig":
        load_dotenv()
        run_hour_utc = int(os.getenv("BACKTEST_RUN_HOUR_UTC", "12"))
        if not 0 <= run_hour_utc <= 23:
            raise RuntimeError("BACKTEST_RUN_HOUR_UTC must be between 0 and 23")
        workers = int(os.getenv("BACKTEST_WORKERS", "2"))
        if workers < 1:
            raise RuntimeError("BACKTEST_WORKERS must be at least 1")
        return cls(
            cities=csv_list(os.getenv("FORECAST_CITIES"), DEFAULT_CITIES),
            model=os.getenv("BACKTEST_MODEL", "ecmwf_ifs"),
            run_hour_utc=run_hour_utc,
            workers=workers,
            single_run_endpoint=os.getenv(
                "OPEN_METEO_SINGLE_RUN_ENDPOINT",
                "https://single-runs-api.open-meteo.com/v1/forecast",
            ),
            archive_endpoint=os.getenv(
                "OPEN_METEO_ARCHIVE_ENDPOINT",
                "https://archive-api.open-meteo.com/v1/archive",
            ),
        )


@dataclass(frozen=True)
class TelegramConfig:
    api_id: int | None
    api_hash: str | None
    phone: str | None
    session: str
    bot_username: str
    cities: list[str]
    command_template: str
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        load_dotenv()
        raw_api_id = os.getenv("TG_API_ID")
        return cls(
            api_id=int(raw_api_id) if raw_api_id else None,
            api_hash=os.getenv("TG_API_HASH"),
            phone=os.getenv("TG_PHONE"),
            session=os.getenv("TG_SESSION", "weatherscan"),
            bot_username=os.getenv("WEATHER_SCAN_BOT_USERNAME", "@weatherscan_bot"),
            cities=csv_list(os.getenv("WEATHER_SCAN_CITIES"), DEFAULT_CITIES),
            command_template=os.getenv("WEATHER_SCAN_COMMAND_TEMPLATE", "{city}"),
            timeout_seconds=float(os.getenv("WEATHER_SCAN_TIMEOUT_SECONDS", "30")),
        )

    def validate_for_telegram(self) -> None:
        missing = []
        if self.api_id is None:
            missing.append("TG_API_ID")
        if not self.api_hash:
            missing.append("TG_API_HASH")
        if not self.phone:
            missing.append("TG_PHONE")
        if missing:
            names = ", ".join(missing)
            raise RuntimeError(f"Missing Telegram credential environment variable(s): {names}")


@dataclass(frozen=True)
class AppConfig:
    database_path: Path
    open_meteo: OpenMeteoConfig
    backtest: BacktestConfig
    telegram: TelegramConfig

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv()
        return cls(
            database_path=Path(os.getenv("DATABASE_PATH", "data/weather_forecasts.sqlite3")),
            open_meteo=OpenMeteoConfig.from_env(),
            backtest=BacktestConfig.from_env(),
            telegram=TelegramConfig.from_env(),
        )
