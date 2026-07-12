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

For the Polymarket client used by `live-round`, use the project virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

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

## Historical Backtest

Run the previous complete calendar month (on 12 July 2026 this is 1-30 June):

```powershell
$env:PYTHONPATH = "src"
python -m weather_polymarket_bot backtest-month --verbose
```

The backtest uses the archived `ecmwf_ifs` run from 12:00 UTC on the prior day, derives the predicted local daily maximum from its hourly values, and settles against Open-Meteo archive daily maximum temperatures. It records every city-day in SQLite and reports the three-bucket hit rate plus its empirical fair all-in basket cost before fees.

This is forecast-skill validation, not trading PnL: Polymarket historical asks, fills, and fees must be joined before the `80c` entry rule can be evaluated honestly.

## Market-Price Backtest

Run a rough comparison of the market-top-three rule at `80c`, `85c`, and `90c` raw basket caps:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m weather_polymarket_bot backtest-market-month
```

For every configured city-day, this command takes the latest public historical YES mark from the six hours before 12:00 UTC on the preceding day, chooses the three highest marks, and settles against the closed Polymarket event. Weather taker fees are included. Public historical asks and order-book depth are unavailable, so it is deliberately an optimistic rough estimate rather than an executable-fill backtest.

To backtest the weather-model method instead, choose the market bucket nearest the preceding-day ECMWF forecast plus one neighboring bucket on each side:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m weather_polymarket_bot backtest-model-month
```

This uses the same `80c`, `85c`, and `90c` raw-cap comparison and the same historical-mark caveat. It maps Celsius forecasts to either Celsius or Fahrenheit Polymarket bucket labels before selecting the three neighboring outcomes.

## Live Weather Baskets

`live-round` discovers active Polymarket Weather events for today and the next two days. For each configured city, it ranks all tradable outcomes by Polymarket's displayed YES probability and selects only the top three. It buys only when those three YES legs have a combined raw VWAP of at most `80c`.

Shares are dynamic but equal across the three legs. The scanner selects the largest integer number of shares that can be filled from the current ask ladders while keeping the full basket cost at or below `$50`. Each marketable BUY leg must be at least `$1`, which is a Polymarket venue requirement.

Run a public, non-trading scan:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m weather_polymarket_bot live-round
```

Submit live orders:

```powershell
$env:PYTHONPATH = "src"
$env:WEATHER_BOT_ENABLE_LIVE = "1"
.\.venv\Scripts\python.exe -m weather_polymarket_bot live-round --live
```

Live mode requires `POLYMARKET_PRIVATE_KEY` and optionally `POLYMARKET_WALLET_ADDRESS` in the environment. No secret is read during a dry scan. The three legs are submitted together, but Polymarket does not make the batch atomic; if a leg is rejected, the bot immediately FOK-sells any matched residual leg at its then-current best bid. Executed event slugs are saved in SQLite to prevent duplicate entries.

## AI News Research

`news-review` uses an OpenAI-compatible 0-0.pro Chat Completions endpoint to compare RSS or Atom headlines with active Polymarket events. It produces research and a short human-review list only: it cannot create, sign, or submit an order.

```powershell
$env:PYTHONPATH = "src"
$env:AI_API_KEY = "your-0-0-pro-key"
.\.venv\Scripts\python.exe -m weather_polymarket_bot news-review
```

For continuous monitoring, run a bounded watch while testing:

```powershell
.\.venv\Scripts\python.exe -m weather_polymarket_bot news-watch --interval-seconds 300 --max-rounds 12
```

The built-in feeds are BBC World, BBC Business, and Google News top stories; set `NEWS_FEEDS` or pass `--feed` to replace them. The monitor reviews the most active configured number of events, rather than trying to put every market into one model prompt. The LLM sees only public headlines and active event titles. It does not receive Polymarket credentials, and its output is treated as untrusted research rather than an order instruction. A failed feed is reported but does not stop the other feeds from being reviewed.
