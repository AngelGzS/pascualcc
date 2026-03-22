"""Strategy 4: FVG Fill Mean Reversion.

Rules:
- Detect Fair Value Gaps (3-candle imbalances) on 15m timeframe.
- When price returns to fill an FVG during NY session (9:30-14:00):
  a) Bullish FVG being filled (price drops into it) → LONG at FVG midpoint
  b) Bearish FVG being filled (price rises into it) → SHORT at FVG midpoint
- SL: beyond the FVG (below bottom for long, above top for short) + buffer
- TP: 1.5R (mean reversion targets are smaller)
- Only trade FVGs from the last 50 candles (recent imbalances).
- Min FVG size: 0.05% of price.
- Max 2 trades per day.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.multi_strategy import SimpleSignal
from strategies.intraday.session_utils import add_est_columns

STRATEGY_NAME = "FVG Fill (Mean Reversion)"

FVG_MIN_SIZE_PCT = 0.0005
FVG_MAX_AGE = 50  # candles
ENTRY_START_MIN = 570
ENTRY_END_HOUR = 14
SL_BUFFER = 0.001
TP_RR = 1.5
MAX_DAILY = 2


def _detect_fvgs(df: pd.DataFrame) -> list[dict]:
    """Detect FVGs. Returns list of {idx, direction, top, bottom, midpoint}."""
    fvgs = []
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    for i in range(1, len(df) - 1):
        mid_price = closes[i]
        min_gap = mid_price * FVG_MIN_SIZE_PCT

        # Bullish FVG
        if lows[i + 1] > highs[i - 1]:
            gap = lows[i + 1] - highs[i - 1]
            if gap >= min_gap:
                fvgs.append({
                    "idx": i,
                    "direction": "bullish",
                    "top": float(lows[i + 1]),
                    "bottom": float(highs[i - 1]),
                    "midpoint": float(lows[i + 1] + highs[i - 1]) / 2,
                    "filled": False,
                })

        # Bearish FVG
        if highs[i + 1] < lows[i - 1]:
            gap = lows[i - 1] - highs[i + 1]
            if gap >= min_gap:
                fvgs.append({
                    "idx": i,
                    "direction": "bearish",
                    "top": float(lows[i - 1]),
                    "bottom": float(highs[i + 1]),
                    "midpoint": float(lows[i - 1] + highs[i + 1]) / 2,
                    "filled": False,
                })

    return fvgs


def generate_signals(df: pd.DataFrame, symbol: str) -> list[SimpleSignal]:
    df = add_est_columns(df)
    signals: list[SimpleSignal] = []
    trades_today: dict[str, int] = {}

    fvgs = _detect_fvgs(df)

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    timestamps = df["timestamp"].values

    for i in range(len(df)):
        day = df["est_min"].iloc[i]  # just for filtering
        tday = str(df["tday"].iloc[i])
        est_min = int(df["est_min"].iloc[i])
        est_h = int(df["est_hour"].iloc[i])

        if est_min < ENTRY_START_MIN or est_h >= ENTRY_END_HOUR:
            continue
        if int(df["weekday"].iloc[i]) >= 5:
            continue
        if trades_today.get(tday, 0) >= MAX_DAILY:
            continue

        h = float(highs[i])
        l = float(lows[i])

        # Check if candle fills any active FVG
        for fvg in fvgs:
            if fvg["filled"]:
                continue
            if i - fvg["idx"] > FVG_MAX_AGE:
                fvg["filled"] = True  # too old
                continue
            if i <= fvg["idx"] + 1:
                continue  # too fresh

            # Bullish FVG being filled: price drops into the gap
            if fvg["direction"] == "bullish" and l <= fvg["top"] and l >= fvg["bottom"]:
                entry = fvg["midpoint"]
                sl = fvg["bottom"] * (1 - SL_BUFFER)
                risk = entry - sl
                if risk <= 0:
                    continue
                tp = entry + risk * TP_RR

                signals.append(SimpleSignal(
                    timestamp=int(timestamps[i]),
                    bar_index=i,
                    symbol=symbol,
                    direction="long",
                    entry_price=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    strategy_name=STRATEGY_NAME,
                    entry_type="limit",
                    metadata=f"FVG_bull top={fvg['top']:.2f} bot={fvg['bottom']:.2f}",
                ))
                fvg["filled"] = True
                trades_today[tday] = trades_today.get(tday, 0) + 1
                break

            # Bearish FVG being filled: price rises into the gap
            if fvg["direction"] == "bearish" and h >= fvg["bottom"] and h <= fvg["top"]:
                entry = fvg["midpoint"]
                sl = fvg["top"] * (1 + SL_BUFFER)
                risk = sl - entry
                if risk <= 0:
                    continue
                tp = entry - risk * TP_RR

                signals.append(SimpleSignal(
                    timestamp=int(timestamps[i]),
                    bar_index=i,
                    symbol=symbol,
                    direction="short",
                    entry_price=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    strategy_name=STRATEGY_NAME,
                    entry_type="limit",
                    metadata=f"FVG_bear top={fvg['top']:.2f} bot={fvg['bottom']:.2f}",
                ))
                fvg["filled"] = True
                trades_today[tday] = trades_today.get(tday, 0) + 1
                break

        # Update filled status for all FVGs
        for fvg in fvgs:
            if fvg["filled"]:
                continue
            if fvg["direction"] == "bullish" and l < fvg["bottom"]:
                fvg["filled"] = True
            elif fvg["direction"] == "bearish" and h > fvg["top"]:
                fvg["filled"] = True

    return signals
