"""Tests for data layer (storage, fetcher parsing)."""
from __future__ import annotations

import os
import tempfile

import numpy as np
import pandas as pd
import pytest

from data.storage import ParquetStorage, interval_to_ms
from data.fetcher import BinanceFetcher


class TestIntervalToMs:
    def test_15m(self) -> None:
        assert interval_to_ms("15m") == 900_000

    def test_1h(self) -> None:
        assert interval_to_ms("1h") == 3_600_000

    def test_1d(self) -> None:
        assert interval_to_ms("1d") == 86_400_000

    def test_5m(self) -> None:
        assert interval_to_ms("5m") == 300_000


class TestParquetStorage:
    def test_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ParquetStorage(base_dir=tmpdir)
            df = pd.DataFrame({
                "timestamp": [1000, 2000, 3000],
                "open": [1.0, 2.0, 3.0],
                "close": [1.1, 2.1, 3.1],
                "high": [1.2, 2.2, 3.2],
                "low": [0.9, 1.9, 2.9],
                "volume": [100.0, 200.0, 300.0],
            })
            storage.save(df, "BTCUSDT", "15m")
            loaded = storage.load("BTCUSDT", "15m")
            assert len(loaded) == 3
            assert list(loaded["timestamp"]) == [1000, 2000, 3000]

    def test_incremental_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ParquetStorage(base_dir=tmpdir)
            df1 = pd.DataFrame({
                "timestamp": [1000, 2000],
                "close": [1.0, 2.0],
                "open": [1.0, 2.0],
                "high": [1.0, 2.0],
                "low": [1.0, 2.0],
                "volume": [100.0, 200.0],
            })
            df2 = pd.DataFrame({
                "timestamp": [2000, 3000],  # 2000 is duplicate
                "close": [2.5, 3.0],
                "open": [2.0, 3.0],
                "high": [2.0, 3.0],
                "low": [2.0, 3.0],
                "volume": [200.0, 300.0],
            })
            storage.save(df1, "TEST", "15m")
            storage.save(df2, "TEST", "15m")
            loaded = storage.load("TEST", "15m")
            assert len(loaded) == 3  # Deduped
            # Last value for duplicate timestamp should be kept
            assert loaded[loaded["timestamp"] == 2000]["close"].iloc[0] == 2.5

    def test_load_nonexistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ParquetStorage(base_dir=tmpdir)
            df = storage.load("NOEXIST", "15m")
            assert df.empty

    def test_get_last_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ParquetStorage(base_dir=tmpdir)
            assert storage.get_last_timestamp("X", "1m") is None

            df = pd.DataFrame({
                "timestamp": [1000, 2000, 3000],
                "close": [1.0, 2.0, 3.0],
                "open": [1.0, 2.0, 3.0],
                "high": [1.0, 2.0, 3.0],
                "low": [1.0, 2.0, 3.0],
                "volume": [1.0, 2.0, 3.0],
            })
            storage.save(df, "X", "1m")
            assert storage.get_last_timestamp("X", "1m") == 3000

    def test_detect_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            storage = ParquetStorage(base_dir=tmpdir)
            # Gap between 3000 and 10000 (more than 2 intervals missing)
            df = pd.DataFrame({
                "timestamp": [1000, 2000, 3000, 10000, 11000],
            })
            gaps = storage.detect_gaps(df, interval_ms=1000)
            assert len(gaps) == 1
            assert gaps[0] == (3000, 10000)


class TestBinanceFetcherParse:
    def test_parse_klines(self) -> None:
        raw = [
            [
                1499040000000, "0.01634", "0.80000", "0.01575", "0.01577",
                "148976.11", 1499644799999, "2434.19",
                308, "1756.87", "28.46", "ignore",
            ]
        ]
        df = BinanceFetcher._parse_klines(raw)
        assert len(df) == 1
        assert df["timestamp"].iloc[0] == 1499040000000
        assert df["close"].iloc[0] == pytest.approx(0.01577)
        assert df["trades"].iloc[0] == 308

    def test_parse_empty(self) -> None:
        df = BinanceFetcher._parse_klines([])
        assert df.empty
