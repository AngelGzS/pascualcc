"""Strategy 1: Opening Range Breakout (ORB).

Rules:
- Define the opening range as the HIGH and LOW of the first 30 minutes
  of the NY session (9:30-10:00 EST).
- LONG: price breaks above OR high with a full candle close above.
- SHORT: price breaks below OR low with a full candle close below.
- SL: opposite side of the OR.
- TP: 1.5x the OR range (1.5R since SL = 1R = OR range).
- Only trade Mon-Fri.
- Max 1 trade per day.
- Only trade during 10:00-13:00 EST (after OR forms, before lunch chop).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.multi_strategy import SimpleSignal
from strategies.intraday.session_utils import add_est_columns, get_session_candles

STRATEGY_NAME = "ORB (Opening Range Breakout)"

# Config
OR_START_HOUR = 9     # 9:30 EST
OR_START_MIN = 570    # 9*60+30 = 570
OR_END_MIN = 600      # 10*60 = 600
ENTRY_START_HOUR = 10
ENTRY_END_HOUR = 13
TP_MULTIPLIER = 1.5


def generate_signals(df: pd.DataFrame, symbol: str) -> list[SimpleSignal]:
    """Generate ORB signals from 15m OHLCV data."""
    df = add_est_columns(df)
    signals: list[SimpleSignal] = []
    traded_days: set[str] = set()

    all_days = sorted(df["tday"].unique())

    for day in all_days:
        if day in traded_days:
            continue

        # Get OR candles (9:30-10:00 = 2 candles of 15m: 9:30 and 9:45)
        or_mask = (df["tday"] == day) & (df["est_min"] >= OR_START_MIN) & (df["est_min"] < OR_END_MIN)
        or_candles = df[or_mask]
        if len(or_candles) < 2:
            continue

        or_high = float(or_candles["high"].max())
        or_low = float(or_candles["low"].min())
        or_range = or_high - or_low

        if or_range <= 0:
            continue

        # Min range: at least 0.05% of price to avoid noise
        mid = (or_high + or_low) / 2
        if or_range / mid < 0.0005:
            continue

        # Scan entry window candles
        entry_mask = (df["tday"] == day) & (df["est_hour"] >= ENTRY_START_HOUR) & (df["est_hour"] < ENTRY_END_HOUR)
        entry_candles = df[entry_mask]

        for i, (idx, row) in enumerate(entry_candles.iterrows()):
            if day in traded_days:
                break

            close = float(row["close"])
            high = float(row["high"])
            low = float(row["low"])

            # LONG: candle closes above OR high
            if close > or_high and low >= or_low:
                sl = or_low
                risk = close - sl
                if risk <= 0:
                    continue
                tp = close + risk * TP_MULTIPLIER

                signals.append(SimpleSignal(
                    timestamp=int(row["timestamp"]),
                    bar_index=int(idx),
                    symbol=symbol,
                    direction="long",
                    entry_price=close,
                    stop_loss=sl,
                    take_profit=tp,
                    strategy_name=STRATEGY_NAME,
                    entry_type="market",
                    metadata=f"OR_H={or_high:.2f} OR_L={or_low:.2f} range={or_range:.2f}",
                ))
                traded_days.add(day)
                break

            # SHORT: candle closes below OR low
            if close < or_low and high <= or_high:
                sl = or_high
                risk = sl - close
                if risk <= 0:
                    continue
                tp = close - risk * TP_MULTIPLIER

                signals.append(SimpleSignal(
                    timestamp=int(row["timestamp"]),
                    bar_index=int(idx),
                    symbol=symbol,
                    direction="short",
                    entry_price=close,
                    stop_loss=sl,
                    take_profit=tp,
                    strategy_name=STRATEGY_NAME,
                    entry_type="market",
                    metadata=f"OR_H={or_high:.2f} OR_L={or_low:.2f} range={or_range:.2f}",
                ))
                traded_days.add(day)
                break

    return signals
