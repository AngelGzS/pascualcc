"""Tests for all indicator calculations."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from indicators.rsi import calc_rsi
from indicators.mfi import calc_mfi
from indicators.tsi import calc_tsi
from indicators.atr import calc_atr
from indicators.ema import calc_ema
from indicators.calculator import calculate_all_indicators


def _make_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    rng = np.random.RandomState(seed)
    close = 100.0 + np.cumsum(rng.randn(n) * 0.5)
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    open_ = close + rng.randn(n) * 0.3
    volume = rng.uniform(100, 10000, n)

    return pd.DataFrame({
        "timestamp": np.arange(n) * 900_000,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "quote_volume": volume * close,
        "trades": rng.randint(10, 500, n),
        "close_time": np.arange(n) * 900_000 + 899_999,
    })


class TestEMA:
    def test_ema_basic(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = calc_ema(s, period=3)
        assert len(result) == 5
        assert not result.isna().any()

    def test_ema_converges(self) -> None:
        s = pd.Series([10.0] * 50)
        result = calc_ema(s, period=20)
        assert abs(result.iloc[-1] - 10.0) < 1e-10

    def test_ema_responds_to_trend(self) -> None:
        s = pd.Series(range(1, 101), dtype=float)
        result = calc_ema(s, period=10)
        # EMA should be below the latest value in an uptrend
        assert result.iloc[-1] < s.iloc[-1]
        assert result.iloc[-1] > s.iloc[0]


class TestRSI:
    def test_rsi_range(self) -> None:
        df = _make_ohlcv(200)
        rsi = calc_rsi(df["close"], period=14)
        valid = rsi.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_rsi_constant_price(self) -> None:
        s = pd.Series([50.0] * 100)
        rsi = calc_rsi(s, period=14)
        # Constant price -> no gains or losses -> RSI should be NaN or 50-ish
        # With EMA method, 0/0 produces NaN
        valid = rsi.dropna()
        # After warmup, RSI of flat series converges (initial diff = 0)
        # First value is NaN (diff), subsequent are NaN due to 0/0
        assert len(rsi) == 100

    def test_rsi_strong_uptrend(self) -> None:
        s = pd.Series(np.linspace(10, 100, 100))
        rsi = calc_rsi(s, period=14)
        # Strong uptrend -> RSI should be high (close to 100)
        assert rsi.iloc[-1] > 90


class TestMFI:
    def test_mfi_range(self) -> None:
        df = _make_ohlcv(200)
        mfi = calc_mfi(df, period=14)
        valid = mfi.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_mfi_needs_volume(self) -> None:
        df = _make_ohlcv(200)
        df["volume"] = 0.0
        mfi = calc_mfi(df, period=14)
        # Zero volume -> 0 flows -> NaN ratios
        # This is expected behavior
        assert len(mfi) == 200


class TestTSI:
    def test_tsi_range(self) -> None:
        df = _make_ohlcv(200)
        tsi = calc_tsi(df["close"], long_period=25, short_period=13)
        valid = tsi.dropna()
        assert (valid >= -100).all()
        assert (valid <= 100).all()

    def test_tsi_uptrend(self) -> None:
        s = pd.Series(np.linspace(10, 100, 200))
        tsi = calc_tsi(s, long_period=25, short_period=13)
        # Strong uptrend -> TSI should be positive
        assert tsi.iloc[-1] > 0


class TestATR:
    def test_atr_positive(self) -> None:
        df = _make_ohlcv(200)
        atr = calc_atr(df, period=14)
        valid = atr.dropna()
        assert (valid > 0).all()

    def test_atr_flat_market(self) -> None:
        n = 100
        df = pd.DataFrame({
            "high": [100.5] * n,
            "low": [99.5] * n,
            "close": [100.0] * n,
        })
        atr = calc_atr(df, period=14)
        # Flat market with constant range -> ATR should converge to 1.0
        assert abs(atr.iloc[-1] - 1.0) < 0.1


class TestCalculator:
    def test_all_indicators_added(self) -> None:
        df = _make_ohlcv(300)
        result = calculate_all_indicators(df)
        expected_cols = ["rsi", "mfi", "tsi", "tsi_signal", "atr", "ema_20", "ema_50", "ema_200"]
        for col in expected_cols:
            assert col in result.columns, f"Missing column: {col}"

    def test_original_columns_preserved(self) -> None:
        df = _make_ohlcv(300)
        original_cols = list(df.columns)
        result = calculate_all_indicators(df)
        for col in original_cols:
            assert col in result.columns

    def test_does_not_modify_input(self) -> None:
        df = _make_ohlcv(300)
        cols_before = list(df.columns)
        _ = calculate_all_indicators(df)
        assert list(df.columns) == cols_before
