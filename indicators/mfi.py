"""Money Flow Index calculation."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def calc_mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Money Flow Index.

    Formula:
        typical_price = (high + low + close) / 3
        raw_money_flow = typical_price * volume
        positive_flow = raw_money_flow where typical_price > typical_price[i-1]
        negative_flow = raw_money_flow where typical_price < typical_price[i-1]
        money_ratio = sum(positive_flow, period) / sum(negative_flow, period)
        MFI = 100 - (100 / (1 + money_ratio))
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    raw_money_flow = typical_price * df["volume"]

    tp_diff = typical_price.diff()
    positive_flow = raw_money_flow.where(tp_diff > 0, 0.0)
    negative_flow = raw_money_flow.where(tp_diff < 0, 0.0)

    positive_sum = positive_flow.rolling(window=period).sum()
    negative_sum = negative_flow.rolling(window=period).sum()

    money_ratio = positive_sum / negative_sum.replace(0, np.nan)
    mfi = 100.0 - (100.0 / (1.0 + money_ratio))

    return mfi
