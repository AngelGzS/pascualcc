"""Local Parquet storage for OHLCV data."""
from __future__ import annotations

import logging
import os

import pandas as pd

from config import settings

logger = logging.getLogger(__name__)


class ParquetStorage:
    """Read/write OHLCV DataFrames as Parquet files with gap detection."""

    def __init__(self, base_dir: str = settings.RAW_DIR) -> None:
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def _filepath(self, symbol: str, interval: str) -> str:
        return os.path.join(self.base_dir, f"{symbol}_{interval}.parquet")

    def save(self, df: pd.DataFrame, symbol: str, interval: str) -> None:
        """Save DataFrame to Parquet, merging with existing data if present."""
        path = self._filepath(symbol, interval)
        if os.path.exists(path):
            existing = pd.read_parquet(path)
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(subset="timestamp", keep="last")
            df = df.sort_values("timestamp").reset_index(drop=True)
        df.to_parquet(path, index=False, engine="pyarrow")
        logger.info("Saved %d candles to %s", len(df), path)

    def load(self, symbol: str, interval: str) -> pd.DataFrame:
        """Load DataFrame from Parquet. Returns empty DataFrame if not found."""
        path = self._filepath(symbol, interval)
        if not os.path.exists(path):
            logger.warning("No data file found: %s", path)
            return pd.DataFrame()
        df = pd.read_parquet(path, engine="pyarrow")
        logger.info("Loaded %d candles from %s", len(df), path)
        return df

    def get_last_timestamp(self, symbol: str, interval: str) -> int | None:
        """Get the last timestamp stored for incremental fetching."""
        df = self.load(symbol, interval)
        if df.empty:
            return None
        return int(df["timestamp"].iloc[-1])

    def detect_gaps(self, df: pd.DataFrame, interval_ms: int) -> list[tuple[int, int]]:
        """Detect gaps in the data where timestamps are not contiguous.

        Args:
            df: DataFrame with 'timestamp' column
            interval_ms: expected interval in milliseconds (e.g. 900000 for 15m)

        Returns:
            List of (gap_start_ms, gap_end_ms) tuples
        """
        if df.empty or len(df) < 2:
            return []

        timestamps = df["timestamp"].values
        gaps: list[tuple[int, int]] = []

        for i in range(1, len(timestamps)):
            expected = timestamps[i - 1] + interval_ms
            if timestamps[i] > expected + interval_ms:  # More than 1 interval missing
                gaps.append((int(timestamps[i - 1]), int(timestamps[i])))
                logger.warning(
                    "Gap detected: %d to %d (%d candles missing)",
                    timestamps[i - 1], timestamps[i],
                    (timestamps[i] - timestamps[i - 1]) // interval_ms - 1,
                )

        return gaps


def interval_to_ms(interval: str) -> int:
    """Convert Binance interval string to milliseconds."""
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    numeric = int(interval[:-1])
    unit = interval[-1]
    return numeric * units[unit]
