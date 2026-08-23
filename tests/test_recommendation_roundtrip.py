"""Roundtrip tests for the US screener recommendation pipeline."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from us_screener import (
    PRESETS,
    ScreenerConfig,
    StockMetrics,
    TradeRecommendation,
    filter_candidates,
    generate_recommendation,
)


def test_recommendation_roundtrip():
    rec = generate_recommendation(
        ticker="AAPL",
        close=200.0,
        ma20=195.0,
        atr14=4.0,
        rsi14=60.0,
    )
    assert isinstance(rec, TradeRecommendation)
    assert rec.stop_loss < rec.close
    assert rec.target_profit > rec.close
    assert rec.risk_reward >= 2.0 - 1e-9

    payload = json.loads(json.dumps(asdict(rec)))
    rebuilt = TradeRecommendation(**payload)
    assert rebuilt == rec


def test_stock_metrics_from_dict_with_nested_rec():
    rec = generate_recommendation("MSFT", 400.0, 390.0, 8.0, 55.0)
    metrics = StockMetrics(
        ticker="MSFT",
        close=400.0,
        volume=20_000_000,
        value_traded=8_000_000_000.0,
        avg_volume_20d=10_000_000.0,
        volume_spike=2.0,
        ma20=390.0,
        above_ma20=True,
        rsi14=55.0,
        macd=1.5,
        macd_signal=1.2,
        atr14=8.0,
        latest_date="2026-08-21",
        recommendation=rec,
    )
    data = json.loads(json.dumps(asdict(metrics)))
    rebuilt = StockMetrics.from_dict(data)
    assert rebuilt.ticker == "MSFT"
    assert isinstance(rebuilt.recommendation, TradeRecommendation)
    assert rebuilt.recommendation.target_profit > rebuilt.recommendation.close


def test_filter_candidates_respects_thresholds():
    config = ScreenerConfig.from_preset("default")
    good = StockMetrics(
        ticker="NVDA", close=150.0, volume=50_000_000,
        value_traded=7_500_000_000.0, avg_volume_20d=20_000_000.0,
        volume_spike=2.5, ma20=140.0, above_ma20=True,
        rsi14=65.0, macd=2.0, macd_signal=1.5, atr14=5.0,
        latest_date="2026-08-21",
    )
    low_liquidity = StockMetrics(
        ticker="SMALL", close=150.0, volume=1_000,
        value_traded=150_000.0, avg_volume_20d=500.0,
        volume_spike=2.0, ma20=140.0, above_ma20=True,
        rsi14=65.0, macd=2.0, macd_signal=1.5, atr14=5.0,
        latest_date="2026-08-21",
    )
    below_ma = StockMetrics(
        ticker="WEAK", close=100.0, volume=5_000_000,
        value_traded=500_000_000.0, avg_volume_20d=2_000_000.0,
        volume_spike=2.5, ma20=110.0, above_ma20=False,
        rsi14=40.0, macd=-1.0, macd_signal=-0.5, atr14=3.0,
        latest_date="2026-08-21",
    )
    penny = StockMetrics(
        ticker="PENNY", close=2.0, volume=100_000_000,
        value_traded=200_000_000.0, avg_volume_20d=10_000_000.0,
        volume_spike=10.0, ma20=1.5, above_ma20=True,
        rsi14=80.0, macd=0.5, macd_signal=0.3, atr14=0.2,
        latest_date="2026-08-21",
    )

    result = filter_candidates([good, low_liquidity, below_ma, penny], config)
    tickers = [m.ticker for m in result]
    assert tickers == ["NVDA"]


def test_presets_valid():
    for name in PRESETS:
        cfg = ScreenerConfig.from_preset(name)
        assert cfg.min_value_traded > 0
        assert cfg.volume_spike_ratio > 0
        assert cfg.top_n > 0


def test_high_rsi_pullback_entry():
    rec = generate_recommendation("TSLA", 250.0, 230.0, 10.0, 75.0)
    # With hot RSI, entry zone top should be at close (wait for pullback)
    assert rec.entry_zone_high == 250.0
    assert rec.entry_zone_low >= 230.0
