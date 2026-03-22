"""Strategy 3: Previous Day High/Low Sweep + Reversal.

Rules:
- Identify Previous Day High (PDH) and Previous Day Low (PDL) from regular session.
- During NY session (9:30-14:00 EST), look for a candle that:
  a) Sweeps PDH (high > PDH) but closes below PDH → SHORT signal
  b) Sweeps PDL (low < PDL) but closes above PDL → LONG signal
- Entry: market at candle close
- SL: beyond the sweep (high of sweep candle for short, low for long) + buffer
- TP: 2R
- Max 1 trade per day.
- Must be a weekday.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.multi_strategy import SimpleSignal
from strategies.intraday.session_utils import add_est_columns, get_prev_day_hl

STRATEGY_NAME = "PDH/PDL Sweep Reversal"

ENTRY_START_MIN = 570  # 9:30
ENTRY_END_HOUR = 14
SL_BUFFER = 0.001
TP_RR = 2.0


def generate_signals(df: pd.DataFrame, symbol: str) -> list[SimpleSignal]:
    df = add_est_columns(df)
    signals: list[SimpleSignal] = []
    traded_days: set[str] = set()
    all_days = sorted(df["tday"].unique())

    for day in all_days:
        if day in traded_days:
            continue

        # Get previous day high/low
        prev_hl = get_prev_day_hl(df, day)
        if prev_hl is None:
            continue
        pdh, pdl = prev_hl

        if pdh <= pdl:
            continue

        # Scan NY session
        mask = (
            (df["tday"] == day)
            & (df["est_min"] >= ENTRY_START_MIN)
            & (df["est_hour"] < ENTRY_END_HOUR)
            & (df["weekday"] < 5)
        )
        session = df[mask]

        for idx, row in session.iterrows():
            if day in traded_days:
                break

            idx_int = int(idx)
            high = float(row["high"])
            low = float(row["low"])
            close = float(row["close"])

            # PDH sweep → SHORT
            if high > pdh and close < pdh:
                sl = high * (1 + SL_BUFFER)
                risk = sl - close
                if risk <= 0:
                    continue
                tp = close - risk * TP_RR

                signals.append(SimpleSignal(
                    timestamp=int(row["timestamp"]),
                    bar_index=idx_int,
                    symbol=symbol,
                    direction="short",
                    entry_price=close,
                    stop_loss=sl,
                    take_profit=tp,
                    strategy_name=STRATEGY_NAME,
                    entry_type="market",
                    metadata=f"PDH={pdh:.2f} swept_to={high:.2f}",
                ))
                traded_days.add(day)
                break

            # PDL sweep → LONG
            if low < pdl and close > pdl:
                sl = low * (1 - SL_BUFFER)
                risk = close - sl
                if risk <= 0:
                    continue
                tp = close + risk * TP_RR

                signals.append(SimpleSignal(
                    timestamp=int(row["timestamp"]),
                    bar_index=idx_int,
                    symbol=symbol,
                    direction="long",
                    entry_price=close,
                    stop_loss=sl,
                    take_profit=tp,
                    strategy_name=STRATEGY_NAME,
                    entry_type="market",
                    metadata=f"PDL={pdl:.2f} swept_to={low:.2f}",
                ))
                traded_days.add(day)
                break

    return signals
