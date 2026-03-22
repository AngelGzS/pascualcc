"""Liquidity sweep detection using swing pivots."""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.po3 import settings
from strategies.po3.types import LiquiditySweep


def find_swing_levels(
    df: pd.DataFrame, left: int, right: int
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Find swing highs and swing lows using left/right pivot windows.

    Swing high: high[i] > all highs in [i-left:i] and [i+1:i+right+1].
    Swing low:  low[i]  < all lows  in [i-left:i] and [i+1:i+right+1].

    Returns:
        (swing_highs, swing_lows) as lists of (bar_index, price) tuples.
    """
    highs = df["high"].values
    lows = df["low"].values
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []

    for i in range(left, len(df) - right):
        # Swing high check
        left_highs = highs[i - left : i]
        right_highs = highs[i + 1 : i + right + 1]
        if len(left_highs) == left and len(right_highs) == right:
            if highs[i] > np.max(left_highs) and highs[i] > np.max(right_highs):
                swing_highs.append((i, float(highs[i])))

        # Swing low check
        left_lows = lows[i - left : i]
        right_lows = lows[i + 1 : i + right + 1]
        if len(left_lows) == left and len(right_lows) == right:
            if lows[i] < np.min(left_lows) and lows[i] < np.min(right_lows):
                swing_lows.append((i, float(lows[i])))

    return swing_highs, swing_lows


def detect_sweeps(
    df: pd.DataFrame, bar_index: int, lookback: int | None = None
) -> list[LiquiditySweep]:
    """Detect liquidity sweeps at the given bar.

    High sweep: candle's wick goes above a swing high but body closes below it.
    Low sweep:  candle's wick goes below a swing low  but body closes above it.

    Checks candle at bar_index against swing levels found in
    [bar_index - lookback : bar_index].

    Args:
        df: OHLCV DataFrame.
        bar_index: Index of the candle to check for sweeps.
        lookback: Number of candles to search for swing levels. Defaults to
            settings.SWEEP_LOOKBACK.

    Returns:
        List of LiquiditySweep objects detected at this bar.
    """
    if lookback is None:
        lookback = settings.SWEEP_LOOKBACK

    sweeps: list[LiquiditySweep] = []

    start = max(0, bar_index - lookback)
    # Need at least PIVOT_LEFT + PIVOT_RIGHT + 1 candles for swing detection
    if bar_index - start < settings.PIVOT_LEFT + settings.PIVOT_RIGHT + 1:
        return sweeps

    # Find swing levels in the lookback window (exclude current bar)
    window = df.iloc[start:bar_index].reset_index(drop=True)
    swing_highs, swing_lows = find_swing_levels(
        window, settings.PIVOT_LEFT, settings.PIVOT_RIGHT
    )

    candle = df.iloc[bar_index]
    candle_open = candle["open"]
    candle_close = candle["close"]
    candle_high = candle["high"]
    candle_low = candle["low"]
    body_top = max(candle_open, candle_close)
    body_bottom = min(candle_open, candle_close)

    # High sweep: wick above swing high, body closes below it
    for _idx, level in swing_highs:
        if candle_high > level and body_top < level:
            sweeps.append(
                LiquiditySweep(
                    timestamp=int(candle["timestamp"]),
                    sweep_type="high_sweep",
                    level_swept=level,
                    sweep_candle_high=candle_high,
                    sweep_candle_low=candle_low,
                    bar_index=bar_index,
                )
            )

    # Low sweep: wick below swing low, body closes above it
    for _idx, level in swing_lows:
        if candle_low < level and body_bottom > level:
            sweeps.append(
                LiquiditySweep(
                    timestamp=int(candle["timestamp"]),
                    sweep_type="low_sweep",
                    level_swept=level,
                    sweep_candle_high=candle_high,
                    sweep_candle_low=candle_low,
                    bar_index=bar_index,
                )
            )

    return sweeps
