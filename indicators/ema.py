"""Exponential Moving Average calculation."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average.

    Formula:
        multiplier = 2 / (period + 1)
        EMA[i] = (value[i] * multiplier) + (EMA[i-1] * (1 - multiplier))

    Uses pandas ewm with adjust=False for recursive EMA (Wilder-style).
    """
    return series.ewm(span=period, adjust=False).mean()
