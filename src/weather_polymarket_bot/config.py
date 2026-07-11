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
    telegram: TelegramConfig

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv()
        return cls(
            database_path=Path(os.getenv("DATABASE_PATH", "data/weather_forecasts.sqlite3")),
            telegram=TelegramConfig.from_env(),
        )

