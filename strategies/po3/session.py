"""EST session utilities and 4H resampling for PO3 strategy."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from strategies.po3 import settings

EST = ZoneInfo(settings.EST_TIMEZONE)


def to_est(ts_ms: int) -> datetime:
    """Convert Unix ms timestamp to EST-aware datetime."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=EST)


def resample_to_4h_est(df_15m: pd.DataFrame) -> pd.DataFrame:
    """Resample 15m OHLCV DataFrame to 4H candles aligned to EST session boundaries.

    EST 4H candles: 2AM, 6AM, 10AM, 2PM, 6PM, 10PM.
    The df_15m must have 'timestamp' column in Unix ms.
    Returns DataFrame with same columns but 4H bars.
    """
    if df_15m.empty:
        return df_15m.copy()

    df = df_15m.copy()
    df["dt_est"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert(
        EST
    )
    df = df.set_index("dt_est")

    # Resample to 4H aligned to NY session boundaries: 2,6,10,14,18,22 local time.
    # The EST/EDT index means pandas applies offset in local time.
    # Default 4H anchors at 0,4,8,12,16,20. We add 6h offset to get 6,10,14,18,22,2.
    # This ensures the 6AM and 10AM candles land exactly on those hours.
    agg = df.resample("4h", offset="6h").agg(
        {
            "timestamp": "first",
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    agg = agg.dropna(subset=["timestamp"])

    # Replace timestamp with the 4H boundary start time (from the index)
    result = agg.reset_index()
    result["timestamp"] = result["dt_est"].apply(
        lambda x: int(x.timestamp() * 1000)
    )
    result = result.drop(columns=["dt_est"])
    return result


def get_candle_est_hour(ts_ms: int) -> int:
    """Get the EST hour of a candle timestamp."""
    return to_est(ts_ms).hour


def is_weekday(ts_ms: int) -> bool:
    """Check if timestamp falls on Mon-Fri (weekday 0-4)."""
    return to_est(ts_ms).weekday() < 5


def get_trading_day(ts_ms: int) -> date:
    """Get the trading date in EST for a timestamp."""
    return to_est(ts_ms).date()
