"""Common session/time utilities for all intraday strategies."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

EST = ZoneInfo("America/New_York")


def to_est(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000, tz=EST)


def est_hour(ts_ms: int) -> int:
    return to_est(ts_ms).hour


def est_minute(ts_ms: int) -> int:
    dt = to_est(ts_ms)
    return dt.hour * 60 + dt.minute


def trading_day(ts_ms: int) -> date:
    return to_est(ts_ms).date()


def is_weekday(ts_ms: int) -> bool:
    return to_est(ts_ms).weekday() < 5


def add_est_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add est_hour, est_minute, trading_day columns to DataFrame."""
    out = df.copy()
    ts = out["timestamp"].values
    out["est_hour"] = [est_hour(int(t)) for t in ts]
    out["est_min"] = [est_minute(int(t)) for t in ts]
    out["tday"] = [str(trading_day(int(t))) for t in ts]
    out["weekday"] = [to_est(int(t)).weekday() for t in ts]
    return out


def get_session_candles(df: pd.DataFrame, day: str, start_hour: int, end_hour: int) -> pd.DataFrame:
    """Get candles for a specific day and time range (EST hours)."""
    mask = (df["tday"] == day) & (df["est_hour"] >= start_hour) & (df["est_hour"] < end_hour)
    return df[mask]


def get_prev_day_hl(df: pd.DataFrame, day: str) -> tuple[float, float] | None:
    """Get previous trading day's high and low from the regular session (9:30-16:00)."""
    all_days = sorted(df["tday"].unique())
    if day not in all_days:
        return None
    all_days_list = list(all_days)
    idx = all_days_list.index(day)
    if idx == 0:
        return None
    prev_day = all_days[idx - 1]
    # Regular session: 9-16 EST
    prev = get_session_candles(df, prev_day, 9, 16)
    if prev.empty:
        return None
    return float(prev["high"].max()), float(prev["low"].min())
