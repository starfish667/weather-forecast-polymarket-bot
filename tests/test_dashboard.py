from __future__ import annotations

from weather_polymarket_bot.config import ZeroZeroConfig
from weather_polymarket_bot.dashboard import (
    DashboardMonitor,
    is_loopback_host,
    preferred_lan_address,
)


def test_dashboard_monitor_starts_with_an_idle_snapshot() -> None:
    monitor = DashboardMonitor(
        config=ZeroZeroConfig(
            api_key=None,
            base_url="https://api.0-0.pro/v1",
            model="gpt-5.5",
            timeout_seconds=30,
            news_feeds=[],
            max_events=5,
            max_headlines_per_feed=5,
        ),
        feeds=[],
        interval_seconds=300,
    )

    snapshot = monitor.snapshot()

    assert snapshot["status"] == "idle"
    assert snapshot["review"] is None
    assert snapshot["headline_count"] == 0


def test_loopback_detection() -> None:
    assert is_loopback_host("127.0.0.1")
    assert not is_loopback_host("0.0.0.0")


def test_preferred_lan_address_skips_virtual_adapter_addresses() -> None:
    assert preferred_lan_address(["198.18.0.1", "192.168.36.1", "192.168.2.229"]) == "192.168.2.229"
