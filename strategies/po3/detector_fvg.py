"""Fair Value Gap detection on any timeframe."""
from __future__ import annotations

import pandas as pd

from strategies.po3 import settings
from strategies.po3.types import FVG


def detect_fvgs(df: pd.DataFrame, timeframe: str) -> list[FVG]:
    """Detect all Fair Value Gaps in OHLCV DataFrame.

    Bullish FVG: candle[i-1].high < candle[i+1].low (gap up).
    Bearish FVG: candle[i-1].low > candle[i+1].high (gap down).

    Filter: FVG size must be >= FVG_MIN_SIZE_PERCENT of price.
    """
    fvgs: list[FVG] = []
    if len(df) < 3:
        return fvgs

    highs = df["high"].values
    lows = df["low"].values
    timestamps = df["timestamp"].values
    closes = df["close"].values

    for i in range(1, len(df) - 1):
        mid_price = closes[i]
        min_gap = mid_price * settings.FVG_MIN_SIZE_PERCENT

        # Bullish FVG: gap between candle 1 high and candle 3 low
        if lows[i + 1] > highs[i - 1]:
            gap_size = lows[i + 1] - highs[i - 1]
            if gap_size >= min_gap:
                top = lows[i + 1]
                bottom = highs[i - 1]
                fvgs.append(
                    FVG(
                        timestamp=int(timestamps[i]),
                        direction="bullish",
                        top=top,
                        bottom=bottom,
                        midpoint=(top + bottom) / 2,
                        timeframe=timeframe,
                        bar_index=i,
                    )
                )

        # Bearish FVG: gap between candle 1 low and candle 3 high
        if highs[i + 1] < lows[i - 1]:
            gap_size = lows[i - 1] - highs[i + 1]
            if gap_size >= min_gap:
                top = lows[i - 1]
                bottom = highs[i + 1]
                fvgs.append(
                    FVG(
                        timestamp=int(timestamps[i]),
                        direction="bearish",
                        top=top,
                        bottom=bottom,
                        midpoint=(top + bottom) / 2,
                        timeframe=timeframe,
                        bar_index=i,
                    )
                )

    return fvgs


def find_nearest_fvg(
    fvgs: list[FVG], price: float, direction: str, max_age: int | None = None
) -> FVG | None:
    """Find the nearest unfilled FVG to the given price in the given direction.

    For bullish bias: look for bearish FVGs below price (acts as support/magnet).
    For bearish bias: look for bullish FVGs above price (acts as resistance/magnet).

    Args:
        fvgs: List of detected FVGs.
        price: Current price to measure distance from.
        direction: 'long' or 'short' — the trade bias.
        max_age: If set, ignore FVGs with bar_index older than this many bars ago.

    Returns:
        Nearest qualifying FVG, or None.
    """
    best: FVG | None = None
    best_dist = float("inf")

    for fvg in fvgs:
        if fvg.filled:
            continue
        if max_age is not None and fvg.bar_index < max_age:
            continue

        if direction == "long" and fvg.direction == "bearish" and fvg.midpoint < price:
            dist = price - fvg.midpoint
            if dist < best_dist:
                best_dist = dist
                best = fvg

        elif direction == "short" and fvg.direction == "bullish" and fvg.midpoint > price:
            dist = fvg.midpoint - price
            if dist < best_dist:
                best_dist = dist
                best = fvg

    return best


def update_fvg_fills(fvgs: list[FVG], candle_high: float, candle_low: float) -> None:
    """Mark FVGs as filled if price trades through them.

    Bullish FVG filled when price drops below its bottom.
    Bearish FVG filled when price rises above its top.
    """
    for fvg in fvgs:
        if fvg.filled:
            continue
        if fvg.direction == "bullish" and candle_low <= fvg.bottom:
            fvg.filled = True
        elif fvg.direction == "bearish" and candle_high >= fvg.top:
            fvg.filled = True
