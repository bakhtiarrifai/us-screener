#!/usr/bin/env python3
"""
US Daily Stock Screener.

Fetches daily OHLCV for the US universe (S&P 500 + Nasdaq-100 + ETFs)
from Yahoo Finance via yfinance, then screens swing-trade candidates by:
- Liquidity: minimum daily dollar volume (default >= $25M)
- Volume spike: latest volume > 1.5x 20-day average
- Momentum: close above MA20
- Risk filter: exclude incomplete data or sub-$5 stocks
- Supporting indicators: RSI(14), MACD, ATR(14)
- Recommendation: entry zone, stop loss, target profit (R:R 1:2)

Output: ranked candidate list with supporting metrics (text or JSON).
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf

from tickers import get_universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

PRESETS = {
    "default": {"min_value_traded": 25_000_000.0, "volume_spike_ratio": 1.5, "min_close_price": 5.0, "top_n": 10},
    "strict": {"min_value_traded": 100_000_000.0, "volume_spike_ratio": 2.0, "min_close_price": 10.0, "top_n": 10},
    "loose": {"min_value_traded": 10_000_000.0, "volume_spike_ratio": 1.2, "min_close_price": 2.0, "top_n": 15},
}

# Risk recommendation constants
SWING_RISK_PERCENT = 0.07       # SL distance 7% from close (fallback)
MIN_REWARD_RISK = 2.0           # minimum R:R 1:2
DEFAULT_ENTRY_BUFFER = 0.015    # entry zone +/-1.5%

# Download batch size: yfinance is more stable with chunks of ~100-200
DOWNLOAD_BATCH_SIZE = 150


@dataclass
class ScreenerConfig:
    min_value_traded: float = 25_000_000.0
    volume_spike_ratio: float = 1.5
    ma_window: int = 20
    min_close_price: float = 5.0
    top_n: int = 10

    @classmethod
    def from_preset(cls, name: str) -> "ScreenerConfig":
        preset = PRESETS.get(name, PRESETS["default"])
        return cls(**preset)

    @classmethod
    def from_file(cls, path: str | Path) -> "ScreenerConfig":
        with open(path) as f:
            data = json.load(f)
        return cls(**data)


@dataclass
class TradeRecommendation:
    ticker: str
    close: float
    entry_zone_low: float
    entry_zone_high: float
    stop_loss: float
    target_profit: float
    risk_reward: float
    rationale: str


@dataclass
class StockMetrics:
    ticker: str
    close: float
    volume: int
    value_traded: float
    avg_volume_20d: float
    volume_spike: float
    ma20: float
    above_ma20: bool
    rsi14: float | None
    macd: float | None
    macd_signal: float | None
    atr14: float | None
    latest_date: str
    recommendation: TradeRecommendation | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "StockMetrics":
        """Build StockMetrics from a dict (e.g. asdict/JSON), incl. nested recommendation."""
        data = dict(data)
        rec = data.get("recommendation")
        if isinstance(rec, dict):
            data["recommendation"] = TradeRecommendation(**rec)
        return cls(**data)


def _compute_rsi(prices: pd.Series, window: int = 14) -> float | None:
    if len(prices) < window + 1:
        return None
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0)).astype(float)
    avg_gain = gain.rolling(window=window).mean().iloc[-1]
    avg_loss = loss.rolling(window=window).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


def _compute_macd(prices: pd.Series) -> tuple[float | None, float | None]:
    if len(prices) < 35:
        return None, None
    ema12 = prices.ewm(span=12, adjust=False).mean()
    ema26 = prices.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return float(macd.iloc[-1]), float(signal.iloc[-1])


def _compute_atr(df: pd.DataFrame, window: int = 14) -> float | None:
    """Average True Range for dynamic SL sizing."""
    if len(df) < window + 1:
        return None
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=window).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else None


def generate_recommendation(
    ticker: str,
    close: float,
    ma20: float,
    atr14: float | None,
    rsi14: float | None,
) -> TradeRecommendation:
    """
    Build entry, SL, TP recommendation for swing trading.
    - Entry zone: around close with small buffer (or pullback toward MA20 if RSI hot)
    - SL: below nearest support (ATR-based or 7%)
    - TP: minimum risk-reward 1:2
    """
    if rsi14 is not None and rsi14 > 70:
        # Don't chase — wait for pullback toward MA20
        entry_zone_high = close
        entry_zone_low = max(close * 0.96, ma20)
    else:
        entry_zone_low = close * (1 - DEFAULT_ENTRY_BUFFER)
        entry_zone_high = close * (1 + DEFAULT_ENTRY_BUFFER)

    if atr14 is not None and atr14 > 0:
        sl_distance = max(atr14 * 1.5, close * 0.04)
    else:
        sl_distance = close * SWING_RISK_PERCENT

    stop_loss = close - sl_distance
    tp_distance = sl_distance * MIN_REWARD_RISK
    target_profit = close + tp_distance
    risk_reward = (target_profit - close) / (close - stop_loss)

    if rsi14 is not None and rsi14 > 70:
        rationale = f"Hot RSI ({rsi14:.1f}); wait for pullback into entry zone. ATR-based SL, TP {MIN_REWARD_RISK:.0f}:1."
    else:
        rationale = f"Volume breakout with ATR-based SL. Target TP {MIN_REWARD_RISK:.0f}:1 risk-reward."

    return TradeRecommendation(
        ticker=ticker,
        close=close,
        entry_zone_low=entry_zone_low,
        entry_zone_high=entry_zone_high,
        stop_loss=stop_loss,
        target_profit=target_profit,
        risk_reward=risk_reward,
        rationale=rationale,
    )


def fetch_prices(tickers: list[str], period: str = "90d", progress: bool = False) -> pd.DataFrame:
    """Download OHLCV for a list of Yahoo tickers, in batches."""
    frames: list[pd.DataFrame] = []
    total = len(tickers)

    for start in range(0, total, DOWNLOAD_BATCH_SIZE):
        batch = tickers[start:start + DOWNLOAD_BATCH_SIZE]
        logger.info("Downloading batch %d-%d of %d (period=%s)...",
                    start + 1, min(start + DOWNLOAD_BATCH_SIZE, total), total, period)
        try:
            data = yf.download(batch, period=period, group_by="ticker",
                               progress=False, threads=True, auto_adjust=False)
        except Exception as e:
            logger.warning("Batch starting at %d failed: %s", start, e)
            continue

        if data is None or data.empty:
            logger.warning("Batch starting at %d returned no data.", start)
            continue

        if len(batch) == 1 and "Close" in data.columns:
            tdf = data.copy()
            tdf["Ticker"] = batch[0]
            frames.append(tdf.reset_index())
            continue

        # Multi-ticker: columns are (ticker, field) when group_by="ticker"
        if isinstance(data.columns, pd.MultiIndex):
            level0 = data.columns.get_level_values(0)
            for ticker in batch:
                if ticker not in set(level0):
                    continue
                tdf = data[ticker].copy()
                if tdf.dropna(how="all").empty:
                    continue
                tdf["Ticker"] = ticker
                frames.append(tdf.reset_index())

    if not frames:
        raise ValueError("No data was downloaded successfully.")

    df = pd.concat(frames, ignore_index=True)
    date_col = "Date" if "Date" in df.columns else "Datetime"
    df = df.rename(columns={date_col: "Date"})
    df = df[["Date", "Ticker", "Open", "High", "Low", "Close", "Volume"]]
    df = df.dropna(subset=["Close", "Volume"])
    logger.info("Downloaded %d rows across %d tickers.",
                len(df), df["Ticker"].nunique())
    return df


def calculate_metrics(df: pd.DataFrame, config: ScreenerConfig) -> list[StockMetrics]:
    """Compute per-ticker metrics from an OHLCV DataFrame."""
    metrics: list[StockMetrics] = []

    for ticker, group in df.groupby("Ticker"):
        group = group.sort_values("Date").reset_index(drop=True)
        if len(group) < config.ma_window + 1:
            continue

        latest = group.iloc[-1]
        close = float(latest["Close"])
        volume = int(latest["Volume"])
        value_traded = close * volume

        past = group.iloc[:-config.ma_window]
        if len(past) < config.ma_window:
            continue

        avg_volume_20d = float(past["Volume"].tail(config.ma_window).mean())
        ma20 = float(past["Close"].tail(config.ma_window).mean())
        volume_spike = volume / avg_volume_20d if avg_volume_20d > 0 else 0.0

        close_series = group["Close"]
        rsi14 = _compute_rsi(close_series, window=14)
        macd, macd_signal = _compute_macd(close_series)
        atr14 = _compute_atr(group)

        recommendation = generate_recommendation(
            ticker=ticker,
            close=close,
            ma20=ma20,
            atr14=atr14,
            rsi14=rsi14,
        )

        metrics.append(
            StockMetrics(
                ticker=ticker,
                close=close,
                volume=volume,
                value_traded=value_traded,
                avg_volume_20d=avg_volume_20d,
                volume_spike=volume_spike,
                ma20=ma20,
                above_ma20=close > ma20,
                rsi14=rsi14,
                macd=macd,
                macd_signal=macd_signal,
                atr14=atr14,
                latest_date=pd.to_datetime(latest["Date"]).date().isoformat(),
                recommendation=recommendation,
            )
        )

    return metrics


def filter_candidates(metrics: list[StockMetrics], config: ScreenerConfig) -> list[StockMetrics]:
    """Apply screener filters."""
    candidates = [
        m for m in metrics
        if m.value_traded >= config.min_value_traded
        and m.volume_spike >= config.volume_spike_ratio
        and m.above_ma20
        and m.close >= config.min_close_price
    ]
    candidates.sort(key=lambda x: (x.volume_spike, x.value_traded), reverse=True)
    return candidates[: config.top_n]


def format_currency(value: float) -> str:
    """Format a dollar amount compactly."""
    if value >= 1_000_000_000_000:
        return f"${value/1_000_000_000_000:.2f}T"
    if value >= 1_000_000_000:
        return f"${value/1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value/1_000_000:.2f}M"
    return f"${value:,.0f}"


def print_results(candidates: list[StockMetrics], date_str: str | None = None) -> None:
    if not candidates:
        print(f"\nNo candidates passed the filter on {date_str or 'the latest date'}.\n")
        return

    print(f"\n📊 US Swing Trade Screener — {date_str}\n")
    for m in candidates:
        print(f"{'='*70}")
        print(f"*{m.ticker}*  Close: ${m.close:,.2f}")
        print(f"  Volume Spike: {m.volume_spike:.2f}x | MA20: ${m.ma20:,.2f} | RSI14: {m.rsi14:.1f} | MACD: {m.macd:.2f}")
        if m.recommendation:
            rec = m.recommendation
            print(f"\n  💡 Entry Zone: ${rec.entry_zone_low:,.2f} - ${rec.entry_zone_high:,.2f}")
            print(f"  🛑 Stop Loss:  ${rec.stop_loss:,.2f}")
            print(f"  🎯 Target:     ${rec.target_profit:,.2f}")
            print(f"  ⚖️  R:R:        1:{rec.risk_reward:.2f}")
            print(f"  📝 {rec.rationale}")
    print(f"\nTotal candidates: {len(candidates)}\n")


class HistoryStore:
    """SQLite store for daily screener results."""

    def __init__(self, db_path: str | Path = "screener_history.db"):
        self.db_path = Path(db_path)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS screenings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    value_traded REAL NOT NULL,
                    volume_spike REAL NOT NULL,
                    ma20 REAL NOT NULL,
                    rsi14 REAL,
                    macd REAL,
                    macd_signal REAL,
                    atr14 REAL,
                    entry_zone_low REAL,
                    entry_zone_high REAL,
                    stop_loss REAL,
                    target_profit REAL,
                    risk_reward REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def save(self, date: str, candidates: list[dict]) -> None:
        with sqlite3.connect(self.db_path) as conn:
            if candidates:
                for c in candidates:
                    rec = c.get("recommendation")
                    conn.execute(
                        """
                        INSERT INTO screenings
                        (date, ticker, close, volume, value_traded, volume_spike, ma20, rsi14, macd, macd_signal,
                         atr14, entry_zone_low, entry_zone_high, stop_loss, target_profit, risk_reward)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            date,
                            c["ticker"],
                            c["close"],
                            c["volume"],
                            c["value_traded"],
                            c["volume_spike"],
                            c["ma20"],
                            c.get("rsi14"),
                            c.get("macd"),
                            c.get("macd_signal"),
                            c.get("atr14"),
                            rec.get("entry_zone_low") if rec else None,
                            rec.get("entry_zone_high") if rec else None,
                            rec.get("stop_loss") if rec else None,
                            rec.get("target_profit") if rec else None,
                            rec.get("risk_reward") if rec else None,
                        ),
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO screenings
                    (date, ticker, close, volume, value_traded, volume_spike, ma20, rsi14, macd, macd_signal,
                     atr14, entry_zone_low, entry_zone_high, stop_loss, target_profit, risk_reward)
                    VALUES (?, 'NO_CANDIDATE', 0, 0, 0, 0, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)
                    """,
                    (date,),
                )
            conn.commit()

    def get_recent(self, limit: int = 30) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM screenings ORDER BY date DESC, volume_spike DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]


def _migrate_db(db_path: str | Path) -> None:
    """Add recommendation columns to legacy databases if missing."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("PRAGMA table_info(screenings)")
        columns = [row[1] for row in cursor.fetchall()]
        new_columns = {
            "atr14": "REAL",
            "entry_zone_low": "REAL",
            "entry_zone_high": "REAL",
            "stop_loss": "REAL",
            "target_profit": "REAL",
            "risk_reward": "REAL",
        }
        for col, col_type in new_columns.items():
            if col not in columns:
                conn.execute(f"ALTER TABLE screenings ADD COLUMN {col} {col_type}")
        conn.commit()


def run_screener(
    tickers: list[str],
    config: ScreenerConfig,
    period: str = "90d",
    store: HistoryStore | None = None,
) -> dict:
    """Run the screener pipeline and return results as a dictionary."""
    df = fetch_prices(tickers, period=period)
    metrics = calculate_metrics(df, config)
    candidates = filter_candidates(metrics, config)
    latest_date = pd.to_datetime(df["Date"]).max().date().isoformat() if not df.empty else None

    candidate_dicts = [asdict(m) for m in candidates]

    if store and latest_date:
        store.save(latest_date, candidate_dicts)
        logger.info("Saved %d candidates to SQLite.", len(candidate_dicts))

    return {
        "date": latest_date,
        "candidates": candidate_dicts,
        "total_candidates": len(candidates),
        "universe_size": len(tickers),
        "filters": {
            "min_value_traded": config.min_value_traded,
            "volume_spike_ratio": config.volume_spike_ratio,
            "ma_window": config.ma_window,
            "min_close_price": config.min_close_price,
            "top_n": config.top_n,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="US Daily Stock Screener (S&P 500 + Nasdaq-100 + ETFs)")
    parser.add_argument("--tickers", nargs="+", default=None, help="Explicit Yahoo tickers (overrides universe)")
    parser.add_argument("--universe", type=str, default="all",
                        choices=["all", "sp500", "ndx100", "etfs"],
                        help="Ticker universe to screen")
    parser.add_argument("--refresh-universe", action="store_true",
                        help="Force-refresh constituent lists from Wikipedia")
    parser.add_argument("--min-value", type=float, default=None, help="Minimum daily dollar volume")
    parser.add_argument("--volume-spike", type=float, default=None, help="Minimum volume spike ratio")
    parser.add_argument("--top-n", type=int, default=None, help="Number of top candidates")
    parser.add_argument("--period", type=str, default="90d", help="Yahoo Finance data period")
    parser.add_argument("--preset", type=str, choices=list(PRESETS.keys()), default="default")
    parser.add_argument("--config", type=str, default=None, help="Path to JSON config file")
    parser.add_argument("--output", type=str, choices=["text", "json"], default="text")
    parser.add_argument("--save-history", action="store_true", default=True)
    parser.add_argument("--no-save-history", action="store_true")
    args = parser.parse_args()

    if args.config:
        config = ScreenerConfig.from_file(args.config)
    else:
        config = ScreenerConfig.from_preset(args.preset)

    if args.min_value is not None:
        config.min_value_traded = args.min_value
    if args.volume_spike is not None:
        config.volume_spike_ratio = args.volume_spike
    if args.top_n is not None:
        config.top_n = args.top_n

    if args.tickers:
        tickers = args.tickers
    else:
        tickers = get_universe(
            include_sp500=args.universe in ("all", "sp500"),
            include_ndx100=args.universe in ("all", "ndx100"),
            include_etfs=args.universe in ("all", "etfs"),
            force_refresh=args.refresh_universe,
        )

    store = HistoryStore() if args.save_history and not args.no_save_history else None
    if store:
        _migrate_db(store.db_path)

    result = run_screener(tickers, config, period=args.period, store=store)

    if args.output == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        candidates = [StockMetrics.from_dict(c) for c in result["candidates"]]
        print_results(candidates, result["date"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
