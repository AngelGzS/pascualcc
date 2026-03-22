"""Divergence detection: pivots + regular/hidden divergence classification."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class Divergence:
    """Represents a detected divergence between price and an indicator."""
    bar_index: int           # Index of the current pivot (detection point)
    prev_bar_index: int      # Index of the previous pivot
    indicator: str           # 'rsi', 'mfi', 'tsi'
    div_type: str            # 'regular_bullish', 'regular_bearish', 'hidden_bullish', 'hidden_bearish'
    price_current: float
    price_previous: float
    indicator_current: float
    indicator_previous: float


def find_pivots(
    series: pd.Series,
    left: int = settings.PIVOT_LEFT,
    right: int = settings.PIVOT_RIGHT,
) -> pd.DataFrame:
    """Find pivot highs and lows in a series.

    A pivot high occurs when series[i] is the maximum in [i-left, i+right].
    A pivot low occurs when series[i] is the minimum in [i-left, i+right].

    Returns DataFrame with columns [pivot_high, pivot_low] (NaN where no pivot).
    """
    n = len(series)
    pivot_high = pd.Series(np.nan, index=series.index)
    pivot_low = pd.Series(np.nan, index=series.index)
    values = series.values

    for i in range(left, n - right):
        window = values[i - left: i + right + 1]

        # Check for pivot high
        if values[i] == np.nanmax(window) and not np.isnan(values[i]):
            # Ensure it's strictly the max (not a flat top with equal values at edges)
            is_max = True
            for j in range(len(window)):
                if j != left and window[j] >= values[i] and not np.isnan(window[j]):
                    if window[j] > values[i]:
                        is_max = False
                        break
            if is_max:
                pivot_high.iloc[i] = values[i]

        # Check for pivot low
        if values[i] == np.nanmin(window) and not np.isnan(values[i]):
            is_min = True
            for j in range(len(window)):
                if j != left and window[j] <= values[i] and not np.isnan(window[j]):
                    if window[j] < values[i]:
                        is_min = False
                        break
            if is_min:
                pivot_low.iloc[i] = values[i]

    return pd.DataFrame({"pivot_high": pivot_high, "pivot_low": pivot_low}, index=series.index)


def detect_divergences(
    df: pd.DataFrame,
    indicator_col: str,
    indicator_name: str,
    left: int = settings.PIVOT_LEFT,
    right: int = settings.PIVOT_RIGHT,
    min_distance: int = settings.MIN_PIVOT_DISTANCE,
    max_distance: int = settings.MAX_PIVOT_DISTANCE,
) -> list[Divergence]:
    """Detect all divergences between price and an indicator.

    Finds pivot highs/lows on both price (using 'high'/'low') and indicator,
    then compares slopes to classify divergence type.
    """
    # Find price pivots using high for highs and low for lows
    price_high_pivots = find_pivots(df["high"], left=left, right=right)
    price_low_pivots = find_pivots(df["low"], left=left, right=right)

    indicator_series = df[indicator_col]

    divergences: list[Divergence] = []

    # --- Divergences on LOWS (bullish signals) ---
    low_pivot_indices = price_low_pivots["pivot_low"].dropna().index.tolist()

    for i in range(1, len(low_pivot_indices)):
        curr_idx = low_pivot_indices[i]
        prev_idx = low_pivot_indices[i - 1]

        distance = curr_idx - prev_idx
        if distance < min_distance or distance > max_distance:
            continue

        price_curr = df["low"].iloc[curr_idx]
        price_prev = df["low"].iloc[prev_idx]
        ind_curr = indicator_series.iloc[curr_idx]
        ind_prev = indicator_series.iloc[prev_idx]

        if pd.isna(ind_curr) or pd.isna(ind_prev):
            continue

        price_slope = price_curr - price_prev
        ind_slope = ind_curr - ind_prev

        div_type = None
        # Regular bullish: price lower low, indicator higher low
        if price_slope < 0 and ind_slope > 0:
            div_type = "regular_bullish"
        # Hidden bullish: price higher low, indicator lower low
        elif price_slope > 0 and ind_slope < 0:
            div_type = "hidden_bullish"

        if div_type:
            divergences.append(Divergence(
                bar_index=curr_idx,
                prev_bar_index=prev_idx,
                indicator=indicator_name,
                div_type=div_type,
                price_current=price_curr,
                price_previous=price_prev,
                indicator_current=ind_curr,
                indicator_previous=ind_prev,
            ))

    # --- Divergences on HIGHS (bearish signals) ---
    high_pivot_indices = price_high_pivots["pivot_high"].dropna().index.tolist()

    for i in range(1, len(high_pivot_indices)):
        curr_idx = high_pivot_indices[i]
        prev_idx = high_pivot_indices[i - 1]

        distance = curr_idx - prev_idx
        if distance < min_distance or distance > max_distance:
            continue

        price_curr = df["high"].iloc[curr_idx]
        price_prev = df["high"].iloc[prev_idx]
        ind_curr = indicator_series.iloc[curr_idx]
        ind_prev = indicator_series.iloc[prev_idx]

        if pd.isna(ind_curr) or pd.isna(ind_prev):
            continue

        price_slope = price_curr - price_prev
        ind_slope = ind_curr - ind_prev

        div_type = None
        # Regular bearish: price higher high, indicator lower high
        if price_slope > 0 and ind_slope < 0:
            div_type = "regular_bearish"
        # Hidden bearish: price lower high, indicator higher high
        elif price_slope < 0 and ind_slope > 0:
            div_type = "hidden_bearish"

        if div_type:
            divergences.append(Divergence(
                bar_index=curr_idx,
                prev_bar_index=prev_idx,
                indicator=indicator_name,
                div_type=div_type,
                price_current=price_curr,
                price_previous=price_prev,
                indicator_current=ind_curr,
                indicator_previous=ind_prev,
            ))

    logger.debug("Found %d %s divergences", len(divergences), indicator_name)
    return divergences
