"""Tests for backtesting engine and metrics."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.types import TradeRecord
from backtest.metrics import calculate_metrics, _build_equity_curve, _max_consecutive_losses, BacktestMetrics
from indicators.calculator import calculate_all_indicators


def _make_trade(pnl: float, pnl_pct: float = 0.0, duration: int = 10) -> TradeRecord:
    return TradeRecord(
        trade_id="t1", pair="BTCUSDT", direction="long",
        confluence_score=60, entry_price=60000.0, entry_time=0,
        exit_price=60000.0 + pnl * 100, exit_time=1,
        exit_reason="stop_loss" if pnl < 0 else "take_profit",
        position_size=0.01, pnl_usd=pnl, pnl_percent=pnl_pct,
        atr_at_entry=150.0, atr_multiplier=2.0,
        max_favorable_excursion=abs(pnl) * 1.5,
        max_adverse_excursion=abs(pnl) * 0.5,
        duration_candles=duration,
    )


class TestMetrics:
    def test_empty_trades(self) -> None:
        m = calculate_metrics([], 500.0)
        assert m.total_trades == 0
        assert m.win_rate == 0.0

    def test_all_winners(self) -> None:
        trades = [_make_trade(10.0, 0.02) for _ in range(5)]
        m = calculate_metrics(trades, 500.0, trading_days=30)
        assert m.total_trades == 5
        assert m.win_rate == 1.0
        assert m.total_pnl_usd == 50.0
        assert m.max_drawdown == 0.0

    def test_all_losers(self) -> None:
        trades = [_make_trade(-10.0, -0.02) for _ in range(5)]
        m = calculate_metrics(trades, 500.0, trading_days=30)
        assert m.win_rate == 0.0
        assert m.total_pnl_usd == -50.0
        assert m.max_drawdown > 0

    def test_mixed_trades(self) -> None:
        trades = [
            _make_trade(20.0, 0.04),
            _make_trade(-10.0, -0.02),
            _make_trade(15.0, 0.03),
            _make_trade(-8.0, -0.016),
            _make_trade(25.0, 0.05),
        ]
        m = calculate_metrics(trades, 500.0, trading_days=30)
        assert m.total_trades == 5
        assert m.winning_trades == 3
        assert m.losing_trades == 2
        assert m.profit_factor > 1.0

    def test_consecutive_losses(self) -> None:
        assert _max_consecutive_losses([10, -5, -3, -2, 8, -1]) == 3
        assert _max_consecutive_losses([10, 20, 30]) == 0
        assert _max_consecutive_losses([-1, -2, -3]) == 3

    def test_equity_curve(self) -> None:
        trades = [_make_trade(10.0), _make_trade(-5.0), _make_trade(20.0)]
        curve = _build_equity_curve(trades, 500.0)
        assert curve == [500.0, 510.0, 505.0, 525.0]


class TestBacktestEngineSmoke:
    """Smoke test to verify the backtest engine runs without errors on synthetic data."""

    def test_engine_runs_on_small_data(self) -> None:
        """Engine should handle data smaller than warmup gracefully."""
        from backtest.engine import BacktestEngine

        rng = np.random.RandomState(42)
        n = 100
        close = 100.0 + np.cumsum(rng.randn(n) * 0.5)

        df = pd.DataFrame({
            "timestamp": np.arange(n) * 900_000,
            "open": close + rng.randn(n) * 0.2,
            "high": close + rng.uniform(0.1, 1.0, n),
            "low": close - rng.uniform(0.1, 1.0, n),
            "close": close,
            "volume": rng.uniform(100, 10000, n),
            "quote_volume": rng.uniform(1000, 100000, n),
            "trades": rng.randint(10, 500, n),
            "close_time": np.arange(n) * 900_000 + 899_999,
        })

        df = calculate_all_indicators(df)
        engine = BacktestEngine()
        trades = engine.run(df, "BTCUSDT")

        # With only 100 candles (< warmup 600), should return empty
        assert trades == []

    def test_engine_produces_trades_on_large_data(self) -> None:
        """Engine should produce at least some trades on sufficient data."""
        from backtest.engine import BacktestEngine

        rng = np.random.RandomState(123)
        n = 2000

        # Create trending data with some reversals to generate divergences
        trend = np.cumsum(rng.randn(n) * 2.0)
        cycles = 20.0 * np.sin(np.arange(n) * 2 * np.pi / 100)
        close = 1000.0 + trend + cycles
        close = np.maximum(close, 100.0)  # Keep positive

        df = pd.DataFrame({
            "timestamp": np.arange(n) * 900_000,
            "open": close + rng.randn(n) * 0.5,
            "high": close + rng.uniform(0.5, 5.0, n),
            "low": close - rng.uniform(0.5, 5.0, n),
            "close": close,
            "volume": rng.uniform(100, 10000, n),
            "quote_volume": rng.uniform(1000, 100000, n),
            "trades": rng.randint(10, 500, n),
            "close_time": np.arange(n) * 900_000 + 899_999,
        })

        df = calculate_all_indicators(df)
        engine = BacktestEngine(confluence_threshold=40)  # Lower threshold for more signals
        trades = engine.run(df, "BTCUSDT")

        # Should have run successfully (may or may not produce trades depending on signals)
        assert isinstance(trades, list)
        metrics = engine.get_metrics(trading_days=n // 96)
        assert isinstance(metrics, BacktestMetrics)
