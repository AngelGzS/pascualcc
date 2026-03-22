"""Change in State of Delivery detection.

CISD = market structure shift + displacement candle + FVG formation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.po3 import settings
from strategies.po3.detector_fvg import detect_fvgs
from strategies.po3.types import CISD, FVG


def detect_cisd(
    df: pd.DataFrame, start_index: int, direction: str
) -> CISD | None:
    """Detect a Change in State of Delivery starting from start_index.

    For bullish CISD (after a low sweep):
        1. Find a displacement candle: large bullish body (> 1.5x avg body of last 20).
        2. The displacement must create a bullish FVG.
        3. Must break above a recent swing high (market structure shift).

    For bearish CISD (after a high sweep):
        1. Find a displacement candle: large bearish body.
        2. Creates a bearish FVG.
        3. Must break below a recent swing low.

    Scans forward from start_index through the available data.

    Args:
        df: OHLCV DataFrame.
        start_index: Bar index to start scanning from.
        direction: 'bullish' or 'bearish'.

    Returns:
        CISD if found, None otherwise.
    """
    if start_index + 2 >= len(df):
        return None

    # Pre-compute recent swing level for structure shift validation
    lookback_start = max(0, start_index - settings.SWEEP_LOOKBACK)
    window = df.iloc[lookback_start:start_index]

    if direction == "bullish":
        # Need a recent swing high to break above
        if len(window) < 3:
            return None
        structure_level = window["high"].rolling(3, center=True).max().max()
    else:
        # Need a recent swing low to break below
        if len(window) < 3:
            return None
        structure_level = window["low"].rolling(3, center=True).min().min()

    # Scan forward from start_index looking for displacement + FVG + structure break
    for i in range(start_index, min(len(df) - 1, start_index + settings.SWEEP_LOOKBACK)):
        if not _is_displacement(df, i, direction):
            continue

        # Check if displacement candle + neighbours form an FVG
        if i < 1 or i + 1 >= len(df):
            continue

        fvg_window = df.iloc[i - 1 : i + 2].reset_index(drop=True)
        fvgs = detect_fvgs(fvg_window, df.attrs.get("timeframe", "15m"))

        matching_fvg: FVG | None = None
        for fvg in fvgs:
            if fvg.direction == direction:
                matching_fvg = fvg
                break

        if matching_fvg is None:
            continue

        # Adjust bar_index back to original DataFrame coordinates
        matching_fvg.bar_index = i

        # Check market structure shift
        candle = df.iloc[i]
        if direction == "bullish" and candle["close"] > structure_level:
            return CISD(
                timestamp=int(candle["timestamp"]),
                direction="bullish",
                displacement_index=i,
                fvg=matching_fvg,
                bar_index=i,
            )
        elif direction == "bearish" and candle["close"] < structure_level:
            return CISD(
                timestamp=int(candle["timestamp"]),
                direction="bearish",
                displacement_index=i,
                fvg=matching_fvg,
                bar_index=i,
            )

    return None


def _is_displacement(df: pd.DataFrame, bar_index: int, direction: str) -> bool:
    """Check if candle at bar_index is a displacement candle.

    Body must be > DISPLACEMENT_BODY_MULT * average body of last
    DISPLACEMENT_LOOKBACK candles. Direction must match (bullish body for
    bullish, bearish body for bearish).
    """
    if bar_index < 1:
        return False

    candle = df.iloc[bar_index]
    body = candle["close"] - candle["open"]

    # Direction check
    if direction == "bullish" and body <= 0:
        return False
    if direction == "bearish" and body >= 0:
        return False

    abs_body = abs(body)

    # Compute average absolute body of prior candles
    lb_start = max(0, bar_index - settings.DISPLACEMENT_LOOKBACK)
    prior = df.iloc[lb_start:bar_index]
    if prior.empty:
        return False

    avg_body = (prior["close"] - prior["open"]).abs().mean()
    if avg_body <= 0:
        return False

    return abs_body > settings.DISPLACEMENT_BODY_MULT * avg_body
