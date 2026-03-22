"""Daily bias determination from the 6AM EST 4H candle (Power of 3 Step 1)."""
from __future__ import annotations

import logging

import pandas as pd

from strategies.po3 import settings
from strategies.po3.detector_fvg import find_nearest_fvg
from strategies.po3.types import FVG

logger = logging.getLogger(__name__)


def determine_bias(
    df_4h: pd.DataFrame,
    bar_index: int,
    htf_fvgs: list[FVG],
) -> tuple[str, FVG] | None:
    """Determine directional bias from the 6AM EST 4H candle.

    Steps:
        1. Get candle at bar_index (6AM) and previous candle (2AM).
        2. Check if 6AM candle swept the 2AM high -> SHORT bias.
           - 6AM high > 2AM high (swept) AND 6AM close < 2AM high (rejected).
        3. Check if 6AM candle swept the 2AM low -> LONG bias.
           - 6AM low < 2AM low (swept) AND 6AM close > 2AM low (rejected).
        4. Validate: 6AM close must be inside/beyond a higher-timeframe FVG.
           - For SHORT: close near/below a bearish HTF FVG.
           - For LONG: close near/above a bullish HTF FVG.

    Args:
        df_4h: 4H OHLCV DataFrame.
        bar_index: Index of the 6AM candle in df_4h.
        htf_fvgs: List of FVGs detected on the 4H timeframe.

    Returns:
        ('long' | 'short', htf_fvg) or None if no valid bias.
    """
    if bar_index < 1 or bar_index >= len(df_4h):
        return None

    candle_6am = df_4h.iloc[bar_index]
    candle_2am = df_4h.iloc[bar_index - 1]

    close_6am = candle_6am["close"]
    high_6am = candle_6am["high"]
    low_6am = candle_6am["low"]
    high_2am = candle_2am["high"]
    low_2am = candle_2am["low"]

    bias: str | None = None

    # High sweep -> SHORT bias
    if high_6am > high_2am and close_6am < high_2am:
        bias = "short"
        logger.debug(
            "6AM candle swept 2AM high (%.2f > %.2f) and rejected -> SHORT bias",
            high_6am,
            high_2am,
        )

    # Low sweep -> LONG bias
    elif low_6am < low_2am and close_6am > low_2am:
        bias = "long"
        logger.debug(
            "6AM candle swept 2AM low (%.2f < %.2f) and rejected -> LONG bias",
            low_6am,
            low_2am,
        )

    if bias is None:
        return None

    # Validate against HTF FVG
    htf_fvg = find_nearest_fvg(htf_fvgs, close_6am, bias)
    if htf_fvg is None:
        logger.debug("No HTF FVG found to validate %s bias at price %.2f", bias, close_6am)
        return None

    logger.info(
        "PO3 bias determined: %s | 6AM close=%.2f | HTF FVG [%.2f - %.2f]",
        bias.upper(),
        close_6am,
        htf_fvg.bottom,
        htf_fvg.top,
    )
    return bias, htf_fvg
