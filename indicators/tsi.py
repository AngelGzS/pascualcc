"""True Strength Index calculation."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from indicators.ema import calc_ema

logger = logging.getLogger(__name__)


def calc_tsi(close: pd.Series, long_period: int = 25, short_period: int = 13) -> pd.Series:
    """Calculate True Strength Index.

    Formula:
        momentum = close[i] - close[i-1]
        double_smoothed_momentum = EMA(EMA(momentum, long_period), short_period)
        double_smoothed_abs = EMA(EMA(|momentum|, long_period), short_period)
        TSI = 100 * (double_smoothed_momentum / double_smoothed_abs)
    """
    momentum = close.diff()
    abs_momentum = momentum.abs()

    double_smoothed_mom = calc_ema(calc_ema(momentum, long_period), short_period)
    double_smoothed_abs = calc_ema(calc_ema(abs_momentum, long_period), short_period)

    tsi = 100.0 * (double_smoothed_mom / double_smoothed_abs.replace(0, np.nan))

    return tsi
