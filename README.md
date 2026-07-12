# Weather Forecast Polymarket Bot

Local-first bot for turning weather forecasts into Polymarket basket orders.

Initial goals:

- ingest forecasts from Telegram weather sources
- map forecasts to Polymarket weather markets
- price nearby temperature buckets as baskets
- start in dry-run / paper-trading mode
- keep live trading behind explicit environment flags

## Local Setup

```powershell
Copy-Item .env.example .env
```

The MVP uses Open-Meteo, which does not require an API key. `.env` is optional; use it to change cities, forecast days, or the database path.

## Forecast Round

Run a sample parser round:

```powershell
$env:PYTHONPATH = "src"
python -m weather_polymarket_bot parse-sample --save
```

Run one real Open-Meteo forecast round:

```powershell
$env:PYTHONPATH = "src"
python -m weather_polymarket_bot open-meteo-round
```

Show recent stored forecasts:

```powershell
$env:PYTHONPATH = "src"
python -m weather_polymarket_bot recent
```

Open-Meteo is the default source because it has simple JSON, global city coverage, no auth flow, and both forecast and archive endpoints for later backtests. For higher precision, the next layer can add direct ECMWF/NOAA ensemble data.

## Basket Rule

For a forecast such as `12.8C`, the first basket candidate is the rounded bucket plus one neighbor on each side:

```text
12.8C -> 12C / 13C / 14C
```

The backtest database stores each forecast, the rounded center bucket, and the basket low/high bounds so later market-price snapshots can be joined against it.
