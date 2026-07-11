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
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill `.env` with Telegram API credentials from <https://my.telegram.org/apps>:

```text
TG_API_ID=
TG_API_HASH=
TG_PHONE=
```

This uses a Telegram user client because a normal Telegram bot usually cannot read or message another bot's private chat.

## Forecast Round

Run a sample parser round:

```powershell
$env:PYTHONPATH = "src"
python -m weather_polymarket_bot parse-sample --save
```

Run one real `@weatherscan_bot` round:

```powershell
$env:PYTHONPATH = "src"
python -m weather_polymarket_bot telegram-round
```

The first Telegram run may ask for the login code sent to your Telegram account. It stores a local `.session` file, which is ignored by Git.

## Basket Rule

For a forecast such as `12.8C`, the first basket candidate is the rounded bucket plus one neighbor on each side:

```text
12.8C -> 12C / 13C / 14C
```

The backtest database stores each forecast, the rounded center bucket, and the basket low/high bounds so later market-price snapshots can be joined against it.
