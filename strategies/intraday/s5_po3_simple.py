"""Strategy 5: Simplified PO3 (Power of 3).

Simplified version that removes the strict HTF FVG filter.

Rules:
- Pre-market bias: compare 6AM-9:30AM price action to determine trend.
  If price made higher highs/lows → bullish. Lower highs/lows → bearish.
- During NY session (10:00-13:00 EST), look for:
  a) Liquidity sweep (candle sweeps a prior swing high/low but closes back)
  b) Sweep must agree with pre-market bias
  c) After sweep, look for displacement candle (body > 1.5x average)
- Entry: market at displacement candle close
- SL: beyond the sweep candle extreme
- TP: 2R
- Max 1 trade per day.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.multi_strategy import SimpleSignal
from strategies.intraday.session_utils import add_est_columns, get_session_candles

STRATEGY_NAME = "PO3 Simplified"

PREMARKET_START = 6
PREMARKET_END = 9
ENTRY_START = 10
ENTRY_END = 13
PIVOT_LEFT = 3
PIVOT_RIGHT = 2
BODY_MULT = 1.5
BODY_LOOKBACK = 20
SL_BUFFER = 0.001
TP_RR = 2.0


def _get_premarket_bias(df: pd.DataFrame, day: str) -> str | None:
    """Determine bias from pre-market price action (6AM-9:30 EST)."""
    pm = get_session_candles(df, day, PREMARKET_START, PREMARKET_END + 1)
    if len(pm) < 4:
        return None

    opens = pm["open"].values
    closes = pm["close"].values
    highs = pm["high"].values
    lows = pm["low"].values

    # Simple: if close of pre-market > open of pre-market → bullish
    pm_open = float(opens[0])
    pm_close = float(closes[-1])
    pm_high = float(highs.max())
    pm_low = float(lows.min())

    # Need a clear move (at least 0.1% of price)
    move = abs(pm_close - pm_open) / pm_open
    if move < 0.001:
        return None

    if pm_close > pm_open:
        return "long"
    return "short"


def generate_signals(df: pd.DataFrame, symbol: str) -> list[SimpleSignal]:
    df = add_est_columns(df)
    signals: list[SimpleSignal] = []
    traded_days: set[str] = set()
    all_days = sorted(df["tday"].unique())

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    opens = df["open"].values
    timestamps = df["timestamp"].values

    # Detect swing highs/lows for sweep detection
    pivot_highs: list[tuple[int, float]] = []
    pivot_lows: list[tuple[int, float]] = []

    for i in range(PIVOT_LEFT, len(df) - PIVOT_RIGHT):
        if all(highs[i] >= highs[i - j] for j in range(1, PIVOT_LEFT + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, PIVOT_RIGHT + 1)):
            pivot_highs.append((i, float(highs[i])))
        if all(lows[i] <= lows[i - j] for j in range(1, PIVOT_LEFT + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, PIVOT_RIGHT + 1)):
            pivot_lows.append((i, float(lows[i])))

    bodies = np.abs(closes - opens)

    for day in all_days:
        if day in traded_days:
            continue

        # Get pre-market bias
        bias = _get_premarket_bias(df, day)
        if bias is None:
            continue

        # Scan entry window
        entry = get_session_candles(df, day, ENTRY_START, ENTRY_END)

        for idx, row in entry.iterrows():
            if day in traded_days:
                break

            i = int(idx)
            h = float(row["high"])
            l = float(row["low"])
            c = float(row["close"])
            o = float(row["open"])

            # Check for sweeps
            sweep_type = None
            sweep_level = 0.0
            sweep_extreme = 0.0

            # High sweep → short signal (if bias is short)
            if bias == "short":
                for _, ph_price in pivot_highs:
                    if h > ph_price and c < ph_price:
                        sweep_type = "high_sweep"
                        sweep_level = ph_price
                        sweep_extreme = h
                        break

            # Low sweep → long signal (if bias is long)
            if bias == "long":
                for _, pl_price in pivot_lows:
                    if l < pl_price and c > pl_price:
                        sweep_type = "low_sweep"
                        sweep_level = pl_price
                        sweep_extreme = l
                        break

            if sweep_type is None:
                continue

            # Look for displacement candle after sweep (next 1-4 candles)
            start_b = max(0, i - BODY_LOOKBACK)
            avg_body = float(bodies[start_b:i].mean()) if i > start_b else 0.0
            if avg_body <= 0:
                continue
            threshold = avg_body * BODY_MULT

            for j in range(i + 1, min(i + 5, len(df))):
                body_j = abs(closes[j] - opens[j])
                if body_j < threshold:
                    continue

                # Must agree with direction
                if bias == "long" and closes[j] <= opens[j]:
                    continue
                if bias == "short" and closes[j] >= opens[j]:
                    continue

                # Entry at displacement close
                entry_price = float(closes[j])

                if bias == "long":
                    sl = sweep_extreme * (1 - SL_BUFFER)
                    risk = entry_price - sl
                    if risk <= 0 or risk / entry_price > 0.02:
                        continue
                    tp = entry_price + risk * TP_RR
                else:
                    sl = sweep_extreme * (1 + SL_BUFFER)
                    risk = sl - entry_price
                    if risk <= 0 or risk / entry_price > 0.02:
                        continue
                    tp = entry_price - risk * TP_RR

                signals.append(SimpleSignal(
                    timestamp=int(timestamps[j]),
                    bar_index=j,
                    symbol=symbol,
                    direction=bias,
                    entry_price=entry_price,
                    stop_loss=sl,
                    take_profit=tp,
                    strategy_name=STRATEGY_NAME,
                    entry_type="market",
                    metadata=f"sweep={sweep_type} level={sweep_level:.2f} bias={bias}",
                ))
                traded_days.add(day)
                break

            if day in traded_days:
                break

    return signals
