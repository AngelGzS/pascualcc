"""Market structure: BOS (Break of Structure) and trend context detection."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from config import settings
from signals.divergence import find_pivots

logger = logging.getLogger(__name__)


def detect_bos(
    df: pd.DataFrame,
    left: int = settings.PIVOT_LEFT,
    right: int = settings.PIVOT_RIGHT,
) -> pd.Series:
    """Detect Break of Structure events.

    BOS bullish: price closes above the last Lower High in a bearish trend.
    BOS bearish: price closes below the last Higher Low in a bullish trend.

    Returns Series with values: 'bullish_bos', 'bearish_bos', or NaN.
    """
    pivots_high = find_pivots(df["high"], left=left, right=right)["pivot_high"]
    pivots_low = find_pivots(df["low"], left=left, right=right)["pivot_low"]

    n = len(df)
    bos = pd.Series(np.nan, index=df.index, dtype=object)

    last_swing_high: float | None = None
    last_swing_low: float | None = None
    prev_swing_high: float | None = None
    prev_swing_low: float | None = None
    trend: str = "neutral"  # 'bullish', 'bearish', 'neutral'

    for i in range(n):
        # Update swing points
        if not pd.isna(pivots_high.iloc[i]):
            current_high = pivots_high.iloc[i]
            if last_swing_high is not None:
                if current_high < last_swing_high:
                    # Lower High -> bearish structure
                    if trend == "bullish" or trend == "neutral":
                        trend = "bearish"
                elif current_high > last_swing_high:
                    # Higher High -> bullish structure
                    pass
            prev_swing_high = last_swing_high
            last_swing_high = current_high

        if not pd.isna(pivots_low.iloc[i]):
            current_low = pivots_low.iloc[i]
            if last_swing_low is not None:
                if current_low > last_swing_low:
                    # Higher Low -> bullish structure
                    if trend == "bearish" or trend == "neutral":
                        trend = "bullish"
                elif current_low < last_swing_low:
                    # Lower Low -> bearish structure
                    pass
            prev_swing_low = last_swing_low
            last_swing_low = current_low

        close = df["close"].iloc[i]

        # BOS bullish: in bearish trend, price closes above last swing high (Lower High)
        if trend == "bearish" and last_swing_high is not None:
            if close > last_swing_high:
                bos.iloc[i] = "bullish_bos"
                trend = "bullish"
                logger.debug("Bullish BOS at index %d, close=%.4f > swing_high=%.4f", i, close, last_swing_high)

        # BOS bearish: in bullish trend, price closes below last swing low (Higher Low)
        elif trend == "bullish" and last_swing_low is not None:
            if close < last_swing_low:
                bos.iloc[i] = "bearish_bos"
                trend = "bearish"
                logger.debug("Bearish BOS at index %d, close=%.4f < swing_low=%.4f", i, close, last_swing_low)

    return bos


def get_trend_context(df: pd.DataFrame, index: int) -> str:
    """Determine trend context at a given index using EMAs and BOS.

    Returns: 'bullish', 'bearish', or 'neutral'.
    """
    if index < 0 or index >= len(df):
        return "neutral"

    close = df["close"].iloc[index]
    ema_20 = df["ema_20"].iloc[index]
    ema_50 = df["ema_50"].iloc[index]
    ema_200 = df["ema_200"].iloc[index]

    if pd.isna(ema_20) or pd.isna(ema_50) or pd.isna(ema_200):
        return "neutral"

    # Strong bullish: price > EMA20 > EMA50 > EMA200
    if close > ema_20 > ema_50 > ema_200:
        return "bullish"

    # Strong bearish: price < EMA20 < EMA50 < EMA200
    if close < ema_20 < ema_50 < ema_200:
        return "bearish"

    # Partial bullish
    if close > ema_50 and ema_50 > ema_200:
        return "bullish"

    # Partial bearish
    if close < ema_50 and ema_50 < ema_200:
        return "bearish"

    return "neutral"


def get_ema_alignment(df: pd.DataFrame, index: int, direction: str) -> str:
    """Determine EMA alignment relative to trade direction.

    Returns: 'aligned', 'partial', or 'contra'.
    """
    if index < 0 or index >= len(df):
        return "contra"

    ema_20 = df["ema_20"].iloc[index]
    ema_50 = df["ema_50"].iloc[index]
    ema_200 = df["ema_200"].iloc[index]

    if pd.isna(ema_20) or pd.isna(ema_50) or pd.isna(ema_200):
        return "contra"

    if direction == "long":
        if ema_20 > ema_50 > ema_200:
            return "aligned"
        if ema_20 > ema_50 or ema_50 > ema_200:
            return "partial"
        return "contra"
    else:  # short
        if ema_20 < ema_50 < ema_200:
            return "aligned"
        if ema_20 < ema_50 or ema_50 < ema_200:
            return "partial"
        return "contra"
