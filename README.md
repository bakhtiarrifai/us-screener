# US Daily Stock Screener

Daily stock screener for the **US market** — S&P 500, Nasdaq-100, and a broad ETF universe — built for a **swing trading** approach.

Every weekday at **09:00 ET (30 minutes before the 09:30 US market open)**, the tool pulls daily price & volume data from Yahoo Finance, computes objective filters, and delivers a ranked list of swing-trade candidates with Entry, Stop Loss, and Target Profit recommendations to Telegram.

> **Data note**: uses **previous trading day's close (H-1)**, published day H at 09:00 ET before the open.

Adapted from [idx-screener](https://github.com/bakhtiarrifai/idx-screener) (Indonesia IDX version) — same architecture, US universe & schedule.

## Features

- Daily US data from **Yahoo Finance** via `yfinance` — no API key needed
- Universe: **S&P 500** + **Nasdaq-100** constituents (auto-fetched from Wikipedia, weekly cache, static fallback) + **~250 curated ETFs** (broad market, sectors, thematic, bonds, commodities, leveraged)
- Objective filters: liquidity (dollar volume), volume spike, momentum above MA20
- Supporting indicators: **RSI(14)**, **MACD**, **ATR(14)**
- Trade recommendations: **Entry Zone**, **Stop Loss (SL)**, **Target Profit (TP)** with 1:2 R:R
- Historical storage in **SQLite**
- **Telegram** notifications with log/email fallback
- Automated via **systemd timer** or **cron** (09:00 America/New_York, Mon–Fri)
- Daily health check + **Healthchecks.io** integration
- Web dashboard at http://localhost:5000

## Data Source

- **Yahoo Finance** via [`yfinance`](https://github.com/ranaroussi/yfinance)
- Tickers in plain US format (e.g. `AAPL`, `BRK-B`, `SPY`)

## Filter Criteria

| Filter | Default | Strict | Loose |
|---|---|---|---|
| Daily dollar volume | ≥ $25M | ≥ $100M | ≥ $10M |
| Volume spike | ≥ 1.5x 20-day avg | ≥ 2.0x | ≥ 1.2x |
| Momentum | close > MA20 | close > MA20 | close > MA20 |
| Min close price | ≥ $5 | ≥ $10 | ≥ $2 |
| Top N | 10 | 10 | 15 |

## Installation

```bash
git clone https://github.com/bakhtiarrifai/us-screener.git
cd us-screener

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
```

## Usage

```bash
# Screen the full universe (S&P 500 + Nasdaq-100 + ETFs)
python us_screener.py

# Screen only one segment
python us_screener.py --universe sp500
python us_screener.py --universe ndx100
python us_screener.py --universe etfs

# Force-refresh constituent lists from Wikipedia
python us_screener.py --refresh-universe

# Specific tickers
python us_screener.py --tickers AAPL NVDA TSLA QQQ

# Stricter filter preset + JSON output
python us_screener.py --preset strict --output json

# Full pipeline (screen + Telegram notification)
python run.py
```

## Scheduling (09:00 ET, 30 min before market open)

```bash
bash setup_cron.sh
```

- **systemd**: installs `us-screener.timer` with `OnCalendar=Mon..Fri 09:00:00 America/New_York` and a persistent dashboard service.
- **cron**: installs a job pinned with `CRON_TZ=America/New_York` so the schedule follows US market time regardless of host timezone.

Check status:

```bash
systemctl --user status us-screener.timer
systemctl --user list-timers us-screener.timer
```

## Health Check

```bash
python health_check.py
```

Verifies the systemd timer is active, SQLite has recent data, and the Telegram bot is reachable. Set `HEALTHCHECKS_URL` in `.env` for external monitoring pings.

## Dashboard

```bash
# Standalone US-only dashboard (this repo only)
python dashboard.py
# open http://localhost:5000

# Optional: unified IDX + US dashboard with a market toggle button.
# Requires the idx-screener project checked out next to this one
# (../idx-screener with its own screener_history.db).
python unified_dashboard.py --port 5000
```

Shows candidate history, per-day candidate counts, and average volume spike per ticker. JSON API at `/api/screenings?days=30&ticker=NVDA` (add `&market=idx` on the unified dashboard).

## Project Layout

```
us_screener.py      # Core screener: download, indicators, filters, SQLite history
tickers.py          # Universe builder: S&P 500 + Nasdaq-100 (Wikipedia+cache) + ETFs
run.py              # Cross-platform runner (screen + Telegram)
telegram_sender.py  # Telegram notification with log/email fallback
run_screener.sh     # Daily wrapper (TZ=America/New_York, healthchecks pings)
dashboard.py        # Flask web dashboard (standalone, US only)
unified_dashboard.py # Optional combined IDX + US dashboard with market toggle
health_check.py     # Daily health verification
setup_cron.sh       # systemd timer / cron installer
tests/              # Unit tests
```

## Disclaimer

This is an analysis aid, **not** a trading robot and **not** financial advice. No automatic order execution. Always do your own due diligence before entering any position.
