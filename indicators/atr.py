"""Average True Range calculation."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range using EMA smoothing.

    Formula:
        true_range = max(
            high - low,
            |high - close[i-1]|,
            |low - close[i-1]|
        )
        ATR = EMA(true_range, period)
    """
    prev_close = df["close"].shift(1)

    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = true_range.ewm(span=period, adjust=False).mean()

    return atr
