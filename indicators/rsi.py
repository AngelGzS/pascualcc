"""Relative Strength Index calculation."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate RSI using EMA smoothing (Wilder method).

    Formula:
        change = close[i] - close[i-1]
        gain = change if change > 0, else 0
        loss = |change| if change < 0, else 0
        avg_gain = EMA(gain, period)
        avg_loss = EMA(loss, period)
        RS = avg_gain / avg_loss
        RSI = 100 - (100 / (1 + RS))
    """
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    # Wilder smoothing = EMA with span = period, adjust=False
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()

    # When avg_loss == 0 and avg_gain > 0, RSI = 100
    # When avg_loss == 0 and avg_gain == 0, RSI = 50 (no movement)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))

    # Fill NaN where avg_loss=0: RSI=100 if gaining, 50 if flat
    mask_zero_loss = avg_loss == 0
    rsi = rsi.copy()
    rsi.loc[mask_zero_loss & (avg_gain > 0)] = 100.0
    rsi.loc[mask_zero_loss & (avg_gain == 0)] = 50.0

    return rsi
